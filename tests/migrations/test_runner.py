from __future__ import annotations

import asyncio

import asyncpg

from content_zavod.migrations import SQL_DIR, load_migrations, run_migrations


async def test_run_pending_applies_every_migration_once_and_is_idempotent(
    isolated_pool: asyncpg.Pool,
) -> None:
    applied_first_run = await run_migrations(isolated_pool)
    assert applied_first_run == ["0001_baseline", "0002_dedupe_pending_review_plans"]

    applied_second_run = await run_migrations(isolated_pool)

    assert applied_second_run == []
    recorded = await isolated_pool.fetch("SELECT version FROM schema_migrations ORDER BY version")
    assert [row["version"] for row in recorded] == [
        "0001_baseline",
        "0002_dedupe_pending_review_plans",
    ]


async def test_fresh_install_ends_up_with_the_one_pending_review_per_week_index(
    isolated_pool: asyncpg.Pool,
) -> None:
    await run_migrations(isolated_pool)

    index_exists = await isolated_pool.fetchval(
        "SELECT to_regclass('plans_one_pending_review_per_week_key') IS NOT NULL"
    )

    assert index_exists is True


async def test_upgrade_dedupes_pre_existing_duplicate_pending_review_plans(
    isolated_pool: asyncpg.Pool,
) -> None:
    """The scenario the ticket is about: an installation that has been running
    the old `ensure_schema()`-on-startup baseline (idempotent `CREATE TABLE IF
    NOT EXISTS`, no unique index yet) has accumulated two `pending_review`
    Plans for the same week - the exact data the partial unique index would
    have refused to be created over. Migrating that installation must dedupe
    before enforcing the invariant, not fail startup on it.
    """
    baseline = next(m for m in load_migrations(SQL_DIR) if m.version == "0001_baseline")
    await isolated_pool.execute(baseline.sql)

    older_id, newer_id = "plan-older", "plan-newer"
    await isolated_pool.execute(
        """
        INSERT INTO plans (id, week_label, status, created_at)
        VALUES ($1, 'W33-2026', 'pending_review', now() - interval '1 day'),
               ($2, 'W33-2026', 'pending_review', now())
        """,
        older_id,
        newer_id,
    )
    await isolated_pool.execute(
        "INSERT INTO plan_items (id, plan_id, position, title, status) "
        "VALUES ('item-older', $1, 0, 'Тема A', 'pending_review')",
        older_id,
    )

    applied = await run_migrations(isolated_pool)

    assert "0002_dedupe_pending_review_plans" in applied
    statuses = {
        row["id"]: row["status"]
        for row in await isolated_pool.fetch("SELECT id, status FROM plans")
    }
    assert statuses[older_id] == "archived"
    assert statuses[newer_id] == "pending_review"
    item_status = await isolated_pool.fetchval(
        "SELECT status FROM plan_items WHERE id = 'item-older'"
    )
    assert item_status == "archived"

    # The invariant now holds and is enforced by the index, not just by this run's data.
    with_second_pending_review = await isolated_pool.fetchrow(
        "SELECT to_regclass('plans_one_pending_review_per_week_key') AS idx"
    )
    assert with_second_pending_review["idx"] is not None


async def test_concurrent_run_pending_does_not_race_on_the_tracking_insert(
    isolated_pool: asyncpg.Pool,
) -> None:
    """bot and worker each call `run_migrations` independently at their own
    startup, so a deploy that restarts both at once races two runners
    against the same database. Without serializing, both can see an empty
    `schema_migrations` and both try to insert the same version, crashing
    whichever loses the race on the primary key.
    """
    first_result, second_result = await asyncio.gather(
        run_migrations(isolated_pool), run_migrations(isolated_pool)
    )

    applied_by_either = set(first_result) | set(second_result)
    assert applied_by_either == {"0001_baseline", "0002_dedupe_pending_review_plans"}
    recorded = await isolated_pool.fetch("SELECT version FROM schema_migrations")
    assert len(recorded) == 2


async def test_rollback_of_0002_drops_only_the_index_without_losing_data(
    isolated_pool: asyncpg.Pool,
) -> None:
    """Exercises the rollback ADR-0013 documents for `0002`: dropping the
    index and its tracking row must not touch the archived duplicate Plan or
    its Тема - the dedupe stays, only the migration's own bookkeeping and
    the index go away.
    """
    baseline = next(m for m in load_migrations(SQL_DIR) if m.version == "0001_baseline")
    await isolated_pool.execute(baseline.sql)
    older_id, newer_id = "plan-older", "plan-newer"
    await isolated_pool.execute(
        """
        INSERT INTO plans (id, week_label, status, created_at)
        VALUES ($1, 'W33-2026', 'pending_review', now() - interval '1 day'),
               ($2, 'W33-2026', 'pending_review', now())
        """,
        older_id,
        newer_id,
    )
    await run_migrations(isolated_pool)

    await isolated_pool.execute("DROP INDEX plans_one_pending_review_per_week_key")
    await isolated_pool.execute(
        "DELETE FROM schema_migrations WHERE version = '0002_dedupe_pending_review_plans'"
    )

    index_exists = await isolated_pool.fetchval(
        "SELECT to_regclass('plans_one_pending_review_per_week_key') IS NOT NULL"
    )
    assert index_exists is False
    remaining_versions = {
        row["version"] for row in await isolated_pool.fetch("SELECT version FROM schema_migrations")
    }
    assert remaining_versions == {"0001_baseline"}
    statuses = {
        row["id"]: row["status"]
        for row in await isolated_pool.fetch("SELECT id, status FROM plans")
    }
    assert statuses == {older_id: "archived", newer_id: "pending_review"}
