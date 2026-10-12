"""The Расписание tick: every minute, request this week's Plan for each Пространство whose
deadline has passed (#7, #72, ADR-0015 «Расписание и часовой пояс»).

One interval job instead of a `CronTrigger` per Workspace: the schedule lives only in the
database (`schedule_settings`, `workspaces.timezone`), so changing it on the Расписание
screen is a plain write - nothing in memory to reschedule. There is no separate startup
reconciliation either: the first tick after a restart is the reconciliation, it catches up
a deadline missed while the process was down.

`schedulable_workspaces` is the one query over all Workspaces (an exemption in
`tests/test_tenant_sql.py`). It leaves out a Workspace that

- blocked the bot (`bot_blocked_at`) - nobody would see the Plan;
- has an unfinished Job (queued, running, or finished but not yet delivered) - one
  generation at a time per Workspace, the tick just tries again next minute;
- has no saved Ниша, or is in the middle of Онбординг - `SettingsService` would fill in the
  default Ниша and Направления, so the tick would build a Plan about somebody else's niche
  for whoever pressed `/start` and left (the bug seen on the first start of an empty base).

`weekly_plan_tick` then works out each one's deadline in its own time zone and calls
`Plan.request_new` unless the week already has a Plan. Repeating is safe: the idempotency
key `generate_plan:{week}` is unique per Workspace, so even an empty result (#84, no Plan
created) never starts a second generation in the same week.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol
from zoneinfo import ZoneInfo

import asyncpg
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from ..job_queue import JobId
from ..settings import NICHE_KEY, ONBOARDING_KEY
from ..settings.onboarding import ONBOARDING_STARTED
from ..workspaces import WorkspaceId
from .settings_store import ScheduleConfig

DEFAULT_DAY_OF_WEEK = "mon"
DEFAULT_HOUR = 9
DEFAULT_MINUTE = 0
DEFAULT_SCHEDULE = ScheduleConfig(
    day_of_week=DEFAULT_DAY_OF_WEEK, hour=DEFAULT_HOUR, minute=DEFAULT_MINUTE
)
TICK_INTERVAL = timedelta(minutes=1)
JOB_ID = "weekly_plan_tick"

# The 3-letter day codes `telegram.texts.WEEKDAYS` restricts the Расписание screen and
# `/set_schedule` to; a `day_of_week` outside these 7 keys would KeyError here.
_WEEKDAY_INDEX = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}

logger = logging.getLogger(__name__)


class PlanRequester(Protocol):
    """The two Plan operations the tick needs (see #7, #72)."""

    async def find_active(self, workspace_id: WorkspaceId, week_label: str) -> object | None: ...

    async def request_new(self, workspace_id: WorkspaceId, week_label: str) -> JobId: ...


@dataclass(frozen=True)
class SchedulableWorkspace:
    workspace_id: WorkspaceId
    tz: ZoneInfo
    schedule: ScheduleConfig


def week_label_for(now: datetime, tz: ZoneInfo) -> str:
    iso_year, iso_week, _ = now.astimezone(tz).isocalendar()
    return f"{iso_year}-W{iso_week:02d}"


def due_week(now: datetime, tz: ZoneInfo, schedule: ScheduleConfig) -> str | None:
    """This week's label (in `tz`) once its deadline has passed, `None` before it."""
    local_now = now.astimezone(tz)
    monday_of_this_week = local_now - timedelta(days=local_now.weekday())
    deadline = (monday_of_this_week + timedelta(days=_WEEKDAY_INDEX[schedule.day_of_week])).replace(
        hour=schedule.hour, minute=schedule.minute, second=0, microsecond=0
    )
    if local_now < deadline:
        return None
    return week_label_for(local_now, tz)


async def schedulable_workspaces(pool: asyncpg.Pool) -> list[SchedulableWorkspace]:
    rows = await pool.fetch(
        """
        SELECT w.id, w.timezone, s.day_of_week, s.hour, s.minute
        FROM workspaces w
        LEFT JOIN schedule_settings s ON s.workspace_id = w.id
        WHERE w.bot_blocked_at IS NULL
          AND EXISTS (
              SELECT 1 FROM workspace_settings n WHERE n.workspace_id = w.id AND n.key = $1
          )
          AND NOT EXISTS (
              SELECT 1 FROM workspace_settings o
              WHERE o.workspace_id = w.id AND o.key = $2 AND o.value = $3
          )
          AND NOT EXISTS (
              SELECT 1 FROM jobs j WHERE j.workspace_id = w.id AND j.notified_at IS NULL
          )
        ORDER BY w.id
        """,
        NICHE_KEY,
        ONBOARDING_KEY,
        ONBOARDING_STARTED,
    )
    return [
        SchedulableWorkspace(
            workspace_id=WorkspaceId(row["id"]),
            tz=ZoneInfo(row["timezone"]),
            schedule=(
                DEFAULT_SCHEDULE
                if row["day_of_week"] is None
                else ScheduleConfig(
                    day_of_week=row["day_of_week"], hour=row["hour"], minute=row["minute"]
                )
            ),
        )
        for row in rows
    ]


async def weekly_plan_tick(
    pool: asyncpg.Pool,
    plan: PlanRequester,
    *,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> None:
    current = now()
    for workspace in await schedulable_workspaces(pool):
        week_label = due_week(current, workspace.tz, workspace.schedule)
        if week_label is None:
            continue
        try:
            if await plan.find_active(workspace.workspace_id, week_label) is None:
                await plan.request_new(workspace.workspace_id, week_label)
        except Exception:
            # One Workspace's failure must not starve the ones after it every minute.
            logger.exception("weekly plan tick failed for workspace %s", workspace.workspace_id)


def schedule_weekly_plan_tick(
    scheduler: AsyncIOScheduler, pool: asyncpg.Pool, plan: PlanRequester
) -> None:
    """Every minute, the first run right away - that one catches up a missed deadline."""
    scheduler.add_job(
        weekly_plan_tick,
        IntervalTrigger(seconds=int(TICK_INTERVAL.total_seconds())),
        args=[pool, plan],
        next_run_time=datetime.now(UTC),
        coalesce=True,
        max_instances=1,
        id=JOB_ID,
        replace_existing=True,
    )
