from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from apscheduler.triggers.cron import CronTrigger

from content_zavod.job_queue import JobId
from content_zavod.scheduling import (
    JOB_ID,
    schedule_weekly_plan_trigger,
    trigger_weekly_plan,
    week_label_for,
)

MOSCOW = ZoneInfo("Europe/Moscow")


class FakePlan:
    def __init__(self, job_id: JobId = JobId(1)) -> None:
        self.job_id = job_id
        self.requested: list[str] = []

    async def request_new(self, week_label: str) -> JobId:
        self.requested.append(week_label)
        return self.job_id


class FakeScheduler:
    def __init__(self) -> None:
        self.jobs: list[dict] = []

    def add_job(self, func, trigger, **kwargs) -> None:
        self.jobs.append({"func": func, "trigger": trigger, **kwargs})


def test_week_label_for_formats_as_iso_year_and_week() -> None:
    monday = datetime(2026, 8, 3, 6, 0, tzinfo=timezone.utc)  # 2026-W32 Monday

    assert week_label_for(monday, MOSCOW) == "2026-W32"


def test_week_label_for_uses_the_local_calendar_day_across_the_timezone_boundary() -> None:
    # 2026-08-02 23:30 UTC is already 2026-08-03 02:30 in Moscow (UTC+3): next ISO week.
    sunday_night_utc = datetime(2026, 8, 2, 23, 30, tzinfo=timezone.utc)

    assert week_label_for(sunday_night_utc, MOSCOW) == "2026-W32"


async def test_trigger_weekly_plan_requests_the_current_weeks_label() -> None:
    plan = FakePlan(job_id=JobId(42))
    fixed_now = datetime(2026, 8, 3, 6, 0, tzinfo=timezone.utc)

    job_id = await trigger_weekly_plan(plan, tz=MOSCOW, now=lambda: fixed_now)

    assert plan.requested == ["2026-W32"]
    assert job_id == JobId(42)


def test_schedule_weekly_plan_trigger_registers_a_monday_morning_cron_job_with_catch_up() -> None:
    scheduler = FakeScheduler()
    plan = FakePlan()

    schedule_weekly_plan_trigger(scheduler, plan, tz=MOSCOW)

    assert len(scheduler.jobs) == 1
    job = scheduler.jobs[0]
    assert job["func"] is trigger_weekly_plan
    assert job["args"] == [plan]
    assert job["kwargs"] == {"tz": MOSCOW}
    assert isinstance(job["trigger"], CronTrigger)
    assert job["misfire_grace_time"] == timedelta(hours=6).total_seconds()
    assert job["coalesce"] is True


def test_schedule_weekly_plan_trigger_registers_a_stable_replaceable_job_id() -> None:
    scheduler = FakeScheduler()
    plan = FakePlan()

    schedule_weekly_plan_trigger(scheduler, plan, tz=MOSCOW)

    job = scheduler.jobs[0]
    assert job["id"] == JOB_ID
    assert job["replace_existing"] is True


async def test_the_registered_job_calls_through_to_plan_request_new() -> None:
    scheduler = FakeScheduler()
    plan = FakePlan()

    schedule_weekly_plan_trigger(scheduler, plan, tz=MOSCOW)
    job = scheduler.jobs[0]
    fixed_now = datetime(2026, 8, 3, 6, 0, tzinfo=timezone.utc)

    await job["func"](*job["args"], **job["kwargs"], now=lambda: fixed_now)

    assert plan.requested == ["2026-W32"]
