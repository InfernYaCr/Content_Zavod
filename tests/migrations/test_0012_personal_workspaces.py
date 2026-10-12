"""0012 (ADR-0015): a clean slate on an installation with 0011 data, and the schema-level
guards of personal Workspaces - composite FKs, per-Workspace unique keys, append-only
Учёт расхода."""

from __future__ import annotations

import shutil
from pathlib import Path

import asyncpg
import pytest

from content_zavod.migrations import SQL_DIR, run_migrations


async def _seed_0011_installation(pool: asyncpg.Pool, tmp_path: Path) -> None:
    for path in SQL_DIR.glob("*.sql"):
        if path.stem[:4] <= "0011":
            shutil.copy(path, tmp_path / path.name)
    await run_migrations(pool, tmp_path)
    await pool.execute(
        """
        INSERT INTO plans (id, week_label) VALUES ('p1', '2026-W42');
        INSERT INTO plan_items (id, plan_id, position, title) VALUES ('i1', 'p1', 0, 'Тема');
        INSERT INTO articles (id, plan_id, plan_item_id, title, platform)
            VALUES ('a1', 'p1', 'i1', 'Тема', 'zen');
        INSERT INTO article_versions (article_id, content, prompt, model, tokens, cost)
            VALUES ('a1', 'текст', 'p', 'm', 1, 0);
        INSERT INTO jobs (job_type, payload, idempotency_key)
            SELECT 'generate_plan', '{}', 'k' || n FROM generate_series(1, 7) n;
        INSERT INTO generation_steps
            (job_id, job_type, article_id, step_name, provider, model,
             prompt_template_version, prompt_hash, latency_ms)
            VALUES (1, 'generate_article', 'a1', 'draft', 'yandex', 'm', 'v1', 'h', 10);
        INSERT INTO topic_research (plan_item_id, fingerprint, status, bundle, outline)
            VALUES ('i1', 'f', 'ok', '{}', 'o');
        INSERT INTO pending_inputs (chat_id, user_id, kind, target_id, prompt_message_id)
            VALUES (1, 1, 'article_comment', 'a1', 5);
        INSERT INTO schedule_settings (day_of_week, hour, minute) VALUES ('tue', 10, 0);
        INSERT INTO owner_settings (key, value) VALUES
            ('niche', 'кофейни'), ('onboarding', 'done'), ('asset_file_id:welcome', 'h:f'),
            ('telegraph_access_token', 't'), ('team_note', '-100:7');
        INSERT INTO members (telegram_id, role) VALUES (1, 'owner');
        """
    )


async def test_0012_wipes_user_data_and_keeps_bot_state(
    isolated_pool: asyncpg.Pool, tmp_path: Path
) -> None:
    await _seed_0011_installation(isolated_pool, tmp_path)

    assert await run_migrations(isolated_pool) == ["0012_personal_workspaces"]

    for table in (
        "plans",
        "plan_items",
        "articles",
        "article_versions",
        "generation_steps",
        "topic_research",
        "jobs",
        "pending_inputs",
        "schedule_settings",
        "workspace_settings",
        "limit_charges",
        "workspaces",
    ):
        assert await isolated_pool.fetchval(f"SELECT count(*) FROM {table}") == 0, table
    assert await isolated_pool.fetchval("SELECT to_regclass('owner_settings')") is None
    bot_state = await isolated_pool.fetch("SELECT key FROM bot_state ORDER BY key")
    assert [row["key"] for row in bot_state] == [
        "asset_file_id:welcome",
        "team_note",
        "telegraph_access_token",
    ]
    # Roles stay until the Telegram PR's 0013.
    assert await isolated_pool.fetchval("SELECT count(*) FROM members") == 1

    # No RESTART IDENTITY: a «🔁 Повторить» from before can't hit a new Job's id.
    workspace_id = await isolated_pool.fetchval(
        "INSERT INTO workspaces (owner_telegram_id, timezone) VALUES (-100, 'Europe/Moscow') "
        "RETURNING id"
    )
    new_job_id = await isolated_pool.fetchval(
        "INSERT INTO jobs (workspace_id, job_type, payload, idempotency_key) "
        "VALUES ($1, 'generate_plan', '{}', 'k1') RETURNING id",
        workspace_id,
    )
    assert new_job_id == 8


@pytest.fixture
async def two_fresh_workspaces(isolated_pool: asyncpg.Pool) -> tuple[int, int]:
    await run_migrations(isolated_pool)
    rows = await isolated_pool.fetch(
        "INSERT INTO workspaces (owner_telegram_id, timezone) "
        "VALUES (1, 'Europe/Moscow'), (2, 'Europe/Moscow') RETURNING id"
    )
    return rows[0]["id"], rows[1]["id"]


async def test_a_topic_cannot_join_another_workspaces_plan(
    isolated_pool: asyncpg.Pool, two_fresh_workspaces: tuple[int, int]
) -> None:
    a, b = two_fresh_workspaces
    await isolated_pool.execute(
        "INSERT INTO plans (id, workspace_id, week_label) VALUES ('plan-b', $1, '2026-W42')", b
    )

    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await isolated_pool.execute(
            "INSERT INTO plan_items (id, workspace_id, plan_id, position, title) "
            "VALUES ('item-a', $1, 'plan-b', 0, 'Тема')",
            a,
        )


async def test_week_and_idempotency_keys_are_unique_per_workspace(
    isolated_pool: asyncpg.Pool, two_fresh_workspaces: tuple[int, int]
) -> None:
    a, b = two_fresh_workspaces
    for workspace_id, suffix in ((a, "a"), (b, "b")):
        await isolated_pool.execute(
            "INSERT INTO plans (id, workspace_id, week_label) VALUES ($1, $2, '2026-W42')",
            f"plan-{suffix}",
            workspace_id,
        )
        await isolated_pool.execute(
            "INSERT INTO jobs (workspace_id, job_type, payload, idempotency_key) "
            "VALUES ($1, 'generate_plan', '{}', 'generate_plan:2026-W42')",
            workspace_id,
        )

    with pytest.raises(asyncpg.UniqueViolationError):
        await isolated_pool.execute(
            "INSERT INTO plans (id, workspace_id, week_label) VALUES ('plan-a2', $1, '2026-W42')",
            a,
        )
    with pytest.raises(asyncpg.UniqueViolationError):
        await isolated_pool.execute(
            "INSERT INTO jobs (workspace_id, job_type, payload, idempotency_key) "
            "VALUES ($1, 'generate_plan', '{}', 'generate_plan:2026-W42')",
            a,
        )


async def test_generation_steps_are_append_only(
    isolated_pool: asyncpg.Pool, two_fresh_workspaces: tuple[int, int]
) -> None:
    a, b = two_fresh_workspaces
    job_id = await isolated_pool.fetchval(
        "INSERT INTO jobs (workspace_id, job_type, payload, idempotency_key) "
        "VALUES ($1, 'generate_cover', '{}', 'k') RETURNING id",
        a,
    )
    insert_step = (
        "INSERT INTO generation_steps (workspace_id, job_id, job_type, step_name, provider, "
        "model, prompt_template_version, prompt_hash, latency_ms, unit, quantity) "
        "VALUES ($1, $2, 'generate_cover', 'cover', 'yandex', 'm', 'v1', 'h', 10, 'image', 1)"
    )
    await isolated_pool.execute(insert_step, a, job_id)

    with pytest.raises(asyncpg.RaiseError, match="append-only"):
        await isolated_pool.execute("UPDATE generation_steps SET cost = 0")
    with pytest.raises(asyncpg.RaiseError, match="append-only"):
        await isolated_pool.execute("DELETE FROM generation_steps")
    # A step can't be booked to another Workspace's Job either.
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await isolated_pool.execute(insert_step, b, job_id)
