"""schedule_weekly_plan_trigger: registers the automatic weekly generate_plan trigger (#7).

`week_label_for` and `trigger_weekly_plan` hold all the actual logic and are
plain, fully unit-testable functions; the APScheduler wiring around them is a
thin adapter with nothing worth hiding behind its own interface. A manual
"/generate_plan" bot command is meant to call the same `trigger_weekly_plan`
- the schedule firing and a person asking are just two callers of the same
idempotent operation (`Plan.request_new`).

`misfire_grace_time` + `coalesce=True` give "run once after the VPS was down
over the trigger time" for free, without a bespoke "was this week's Plan
already made" check: `Plan.request_new` is idempotent per week_label anyway.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Callable, Protocol
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from ..job_queue import JobId

DEFAULT_DAY_OF_WEEK = "mon"
DEFAULT_HOUR = 9
DEFAULT_MINUTE = 0
DEFAULT_MISFIRE_GRACE_TIME = timedelta(hours=6)


class PlanTrigger(Protocol):
    """The one Plan operation this module depends on (see #7)."""

    async def request_new(self, week_label: str) -> JobId: ...


def week_label_for(now: datetime, tz: ZoneInfo) -> str:
    iso_year, iso_week, _ = now.astimezone(tz).isocalendar()
    return f"{iso_year}-W{iso_week:02d}"


async def trigger_weekly_plan(
    plan: PlanTrigger,
    *,
    tz: ZoneInfo,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
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
        misfire_grace_time=misfire_grace_time.total_seconds(),
        coalesce=True,
    )
