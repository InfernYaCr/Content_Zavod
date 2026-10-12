"""The Расписание tick against the real database: which Пространства it picks and that it
asks each one's Plan once a week, in its own time zone (ADR-0015)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime

import asyncpg
import pytest_asyncio

from content_zavod.domain import Plan
from content_zavod.job_queue import JobId, JobQueue
from content_zavod.owner_settings import WorkspaceSettingsStore
from content_zavod.scheduling import ScheduleSettings, weekly_plan_tick
from content_zavod.settings import NICHE_KEY, ONBOARDING_KEY
from content_zavod.workspaces import WorkspaceId, Workspaces
from tests.tenancy import WS_A, WS_B

# Monday 2026-10-12 (2026-W42), 03:00 in Moscow = 10:00 in Vladivostok (МСК+7).
MONDAY_0300_MSK = datetime(2026, 10, 12, 0, 0, tzinfo=UTC)
# Monday 12:00 in Moscow: past the default пн 09:00 everywhere in Russia.
MONDAY_NOON_MSK = datetime(2026, 10, 12, 9, 0, tzinfo=UTC)
WEEK = "2026-W42"


@pytest_asyncio.fixture(autouse=True, loop_scope="session")
async def _clean(pool: asyncpg.Pool) -> AsyncIterator[None]:
    await pool.execute(
        "TRUNCATE TABLE jobs, article_versions, articles, plan_items, plans, generation_steps, "
        "workspace_settings, schedule_settings CASCADE"
    )
    await pool.execute(
        "UPDATE workspaces SET timezone = 'Europe/Moscow', bot_blocked_at = NULL "
        "WHERE id = ANY($1::bigint[])",
        [WS_A, WS_B],
    )
    yield


@pytest_asyncio.fixture(loop_scope="session")
async def plan(pool: asyncpg.Pool) -> Plan:
    return Plan(pool, JobQueue(pool))


async def _with_niche(pool: asyncpg.Pool, *workspaces: WorkspaceId) -> None:
    for workspace_id in workspaces:
        await WorkspaceSettingsStore(pool).set(workspace_id, NICHE_KEY, "стоматология")


async def _plan_jobs(pool: asyncpg.Pool) -> list[tuple[int, str]]:
    rows = await pool.fetch(
        "SELECT workspace_id, idempotency_key FROM jobs WHERE job_type = 'generate_plan' "
        "ORDER BY workspace_id, id"
    )
    return [(row["workspace_id"], row["idempotency_key"]) for row in rows]


async def test_each_workspace_gets_its_plan_in_its_own_time_zone(
    pool: asyncpg.Pool, plan: Plan
) -> None:
    await _with_niche(pool, WS_A, WS_B)
    await Workspaces(pool).set_timezone(WS_B, "Asia/Vladivostok")

    await weekly_plan_tick(pool, plan, now=lambda: MONDAY_0300_MSK)

    assert await _plan_jobs(pool) == [(WS_B, f"generate_plan:{WEEK}")]


async def test_a_repeated_tick_in_the_same_week_requests_nothing_more(
    pool: asyncpg.Pool, plan: Plan
) -> None:
    await _with_niche(pool, WS_A)
    await weekly_plan_tick(pool, plan, now=lambda: MONDAY_NOON_MSK)
    # The Job ran and came back empty (#84): delivered, no Plan created.
    await pool.execute("UPDATE jobs SET status = 'done', notified_at = now()")

    await weekly_plan_tick(pool, plan, now=lambda: MONDAY_NOON_MSK)
    await weekly_plan_tick(pool, plan, now=lambda: MONDAY_NOON_MSK)

    assert await _plan_jobs(pool) == [(WS_A, f"generate_plan:{WEEK}")]


async def test_a_week_already_requested_is_not_written_to_again(
    pool: asyncpg.Pool, plan: Plan
) -> None:
    # Past the deadline, the week's Job done and delivered, no active Plan (#84 or archived):
    # the tick must not touch `jobs` every minute (the enqueue upsert is a real UPDATE).
    await _with_niche(pool, WS_A)
    await weekly_plan_tick(pool, plan, now=lambda: MONDAY_NOON_MSK)
    await pool.execute("UPDATE jobs SET status = 'done', notified_at = now()")
    row_version = await pool.fetchval("SELECT xmin::text FROM jobs")

    await weekly_plan_tick(pool, plan, now=lambda: MONDAY_NOON_MSK)

    assert await pool.fetchval("SELECT xmin::text FROM jobs") == row_version


async def test_changing_one_schedule_leaves_the_other_alone(pool: asyncpg.Pool, plan: Plan) -> None:
    await _with_niche(pool, WS_A, WS_B)
    await ScheduleSettings(pool).set(WS_A, "fri", 9, 0)

    await weekly_plan_tick(pool, plan, now=lambda: MONDAY_NOON_MSK)

    assert await _plan_jobs(pool) == [(WS_B, f"generate_plan:{WEEK}")]


async def test_a_fresh_workspace_without_a_niche_gets_no_plan_on_the_startup_catch_up(
    pool: asyncpg.Pool, plan: Plan
) -> None:
    # The live bug: first start on an empty base after the deadline, nobody has done the
    # Онбординг yet - the first tick must not build a Plan from the default Ниша.
    await weekly_plan_tick(pool, plan, now=lambda: MONDAY_NOON_MSK)

    assert await _plan_jobs(pool) == []


async def test_a_workspace_in_the_middle_of_onboarding_is_skipped(
    pool: asyncpg.Pool, plan: Plan
) -> None:
    # The wizard stores the Ниша as soon as it is answered, before «🚀 Запустить».
    await _with_niche(pool, WS_A)
    await WorkspaceSettingsStore(pool).set(WS_A, ONBOARDING_KEY, "started")

    await weekly_plan_tick(pool, plan, now=lambda: MONDAY_NOON_MSK)

    assert await _plan_jobs(pool) == []


async def test_a_workspace_that_blocked_the_bot_is_skipped(pool: asyncpg.Pool, plan: Plan) -> None:
    await _with_niche(pool, WS_A)
    await pool.execute("UPDATE workspaces SET bot_blocked_at = now() WHERE id = $1", WS_A)

    await weekly_plan_tick(pool, plan, now=lambda: MONDAY_NOON_MSK)

    assert await _plan_jobs(pool) == []


async def test_a_workspace_with_an_unfinished_job_is_skipped(
    pool: asyncpg.Pool, plan: Plan
) -> None:
    await _with_niche(pool, WS_A)
    queue = JobQueue(pool)
    await queue.enqueue(WS_A, "generate_article", {}, idempotency_key="article:1")
    # A finished Job whose result is not delivered yet still counts as unfinished.
    await queue.enqueue(WS_A, "generate_cover", {}, idempotency_key="cover:1")
    await pool.execute("UPDATE jobs SET status = 'done' WHERE idempotency_key = 'cover:1'")
    await pool.execute(
        "UPDATE jobs SET status = 'done', notified_at = now() WHERE idempotency_key = 'article:1'"
    )

    await weekly_plan_tick(pool, plan, now=lambda: MONDAY_NOON_MSK)

    assert await _plan_jobs(pool) == []


async def test_a_workspace_with_this_weeks_plan_is_skipped(pool: asyncpg.Pool, plan: Plan) -> None:
    await _with_niche(pool, WS_A)
    await pool.execute(
        "INSERT INTO plans (id, workspace_id, week_label) VALUES ('p1', $1, $2)", WS_A, WEEK
    )

    await weekly_plan_tick(pool, plan, now=lambda: MONDAY_NOON_MSK)

    assert await _plan_jobs(pool) == []


class FailingFor:
    """A Plan that fails for one Workspace - the next ones must still get theirs."""

    def __init__(self, plan: Plan, failing: WorkspaceId) -> None:
        self._plan = plan
        self._failing = failing

    async def find_active(self, workspace_id: WorkspaceId, week_label: str) -> object | None:
        return await self._plan.find_active(workspace_id, week_label)

    async def request_new(self, workspace_id: WorkspaceId, week_label: str) -> JobId:
        if workspace_id == self._failing:
            raise RuntimeError("boom")
        return await self._plan.request_new(workspace_id, week_label)


async def test_one_workspaces_failure_does_not_stop_the_others(
    pool: asyncpg.Pool, plan: Plan
) -> None:
    await _with_niche(pool, WS_A, WS_B)

    await weekly_plan_tick(pool, FailingFor(plan, WS_A), now=lambda: MONDAY_NOON_MSK)

    assert await _plan_jobs(pool) == [(WS_B, f"generate_plan:{WEEK}")]
