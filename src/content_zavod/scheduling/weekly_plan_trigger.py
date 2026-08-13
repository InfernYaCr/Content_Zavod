"""schedule_weekly_plan_trigger: registers the automatic weekly generate_plan trigger (#7).

`week_label_for` and `trigger_weekly_plan` hold all the actual logic and are
plain, fully unit-testable functions; the APScheduler wiring around them is a
thin adapter with nothing worth hiding behind its own interface. A manual
"/generate_plan" bot command is meant to call the same `trigger_weekly_plan`
- the schedule firing and a person asking are just two callers of the same
idempotent operation (`Plan.request_new`).

`misfire_grace_time` + `coalesce=True` give "run once after the VPS was down
over the trigger time" for free *while the process stays up* - but
`AsyncIOScheduler`'s default jobstore is in-memory, so a missed fire time is
forgotten across a process restart. `reconcile_weekly_plan` (#72) covers that
gap: called once at startup, it independently works out whether this week's
deadline has already passed and, if so, requests the week's Plan unless one
already exists - the same idempotent-per-week operation as the live trigger,
just invoked with a "was this already done" guard since there is no
persistent jobstore to ask.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Protocol
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from ..job_queue import JobId

DEFAULT_DAY_OF_WEEK = "mon"
DEFAULT_HOUR = 9
DEFAULT_MINUTE = 0
DEFAULT_MISFIRE_GRACE_TIME = timedelta(hours=6)
JOB_ID = "weekly_plan_trigger"

# Reconciliation re-derives the deadline manually rather than asking a CronTrigger for it
# (no persistent jobstore to query at startup - see module docstring). This mirrors the
# 3-letter day abbreviations `_VALID_DAYS` in telegram/schedule_command.py already restricts
# `/set_schedule` to; a `day_of_week` outside these 7 keys would KeyError here.
_WEEKDAY_INDEX = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}


class PlanTrigger(Protocol):
    """The one Plan operation this module depends on (see #7)."""

    async def request_new(self, week_label: str) -> JobId: ...


class PlanReconciler(PlanTrigger, Protocol):
    """What `reconcile_weekly_plan` needs beyond `PlanTrigger` (see #72): a way to check
    whether the week's Plan already exists, so a restart doesn't request a duplicate."""

    async def find_active(self, week_label: str) -> object | None: ...


def week_label_for(now: datetime, tz: ZoneInfo) -> str:
    iso_year, iso_week, _ = now.astimezone(tz).isocalendar()
    return f"{iso_year}-W{iso_week:02d}"


async def trigger_weekly_plan(
    plan: PlanTrigger,
    *,
    tz: ZoneInfo,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> JobId:
    return await plan.request_new(week_label_for(now(), tz))


def schedule_weekly_plan_trigger(
    scheduler: AsyncIOScheduler,
    plan: PlanTrigger,
    *,
    tz: ZoneInfo,
    day_of_week: str = DEFAULT_DAY_OF_WEEK,
    hour: int = DEFAULT_HOUR,
    minute: int = DEFAULT_MINUTE,
    misfire_grace_time: timedelta = DEFAULT_MISFIRE_GRACE_TIME,
) -> None:
    scheduler.add_job(
        trigger_weekly_plan,
        CronTrigger(day_of_week=day_of_week, hour=hour, minute=minute, timezone=tz),
        args=[plan],
        kwargs={"tz": tz},
        misfire_grace_time=int(misfire_grace_time.total_seconds()),
        coalesce=True,
        id=JOB_ID,
        replace_existing=True,
    )


async def reconcile_weekly_plan(
    plan: PlanReconciler,
    *,
    tz: ZoneInfo,
    day_of_week: str = DEFAULT_DAY_OF_WEEK,
    hour: int = DEFAULT_HOUR,
    minute: int = DEFAULT_MINUTE,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> JobId | None:
    """Recover a weekly trigger missed while the process was down (#72).

    Deliberately silent (returns `None`) both before the week's deadline and
    once its Plan already exists - callers at startup have nothing useful to
    do with either case beyond letting the live cron job take over normally.
    """
    local_now = now().astimezone(tz)
    monday_of_this_week = local_now - timedelta(days=local_now.weekday())
    deadline = (monday_of_this_week + timedelta(days=_WEEKDAY_INDEX[day_of_week])).replace(
        hour=hour, minute=minute, second=0, microsecond=0
    )
    if local_now < deadline:
        return None

    week_label = week_label_for(local_now, tz)
    if await plan.find_active(week_label) is not None:
        return None
    return await plan.request_new(week_label)
