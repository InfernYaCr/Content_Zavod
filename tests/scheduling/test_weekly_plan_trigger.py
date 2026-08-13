from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from apscheduler.triggers.cron import CronTrigger

from content_zavod.job_queue import JobId
from content_zavod.scheduling import (
    JOB_ID,
    reconcile_weekly_plan,
    schedule_weekly_plan_trigger,
    trigger_weekly_plan,
    week_label_for,
)

MOSCOW = ZoneInfo("Europe/Moscow")
DEFAULT_JOB_ID = JobId(1)


class FakePlan:
    def __init__(
        self, job_id: JobId = DEFAULT_JOB_ID, *, active_weeks: set[str] | None = None
    ) -> None:
        self.job_id = job_id
        self.requested: list[str] = []
        self._active_weeks = active_weeks or set()

    async def request_new(self, week_label: str) -> JobId:
        self.requested.append(week_label)
        return self.job_id

    async def find_active(self, week_label: str) -> object | None:
        return object() if week_label in self._active_weeks else None


class FakeScheduler:
    def __init__(self) -> None:
        self.jobs: list[dict] = []

    def add_job(self, func, trigger, **kwargs) -> None:
        self.jobs.append({"func": func, "trigger": trigger, **kwargs})


def test_week_label_for_formats_as_iso_year_and_week() -> None:
    monday = datetime(2026, 8, 3, 6, 0, tzinfo=UTC)  # 2026-W32 Monday

    assert week_label_for(monday, MOSCOW) == "2026-W32"


def test_week_label_for_uses_the_local_calendar_day_across_the_timezone_boundary() -> None:
    # 2026-08-02 23:30 UTC is already 2026-08-03 02:30 in Moscow (UTC+3): next ISO week.
    sunday_night_utc = datetime(2026, 8, 2, 23, 30, tzinfo=UTC)

    assert week_label_for(sunday_night_utc, MOSCOW) == "2026-W32"


async def test_trigger_weekly_plan_requests_the_current_weeks_label() -> None:
    plan = FakePlan(job_id=JobId(42))
    fixed_now = datetime(2026, 8, 3, 6, 0, tzinfo=UTC)

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
    fixed_now = datetime(2026, 8, 3, 6, 0, tzinfo=UTC)

    await job["func"](*job["args"], **job["kwargs"], now=lambda: fixed_now)

    assert plan.requested == ["2026-W32"]


async def test_reconcile_weekly_plan_does_nothing_before_this_weeks_deadline() -> None:
    plan = FakePlan()
    # Monday 2026-08-03 08:00 Moscow, before the default mon 09:00 deadline.
    before_deadline = datetime(2026, 8, 3, 5, 0, tzinfo=UTC)

    result = await reconcile_weekly_plan(plan, tz=MOSCOW, now=lambda: before_deadline)

    assert result is None
    assert plan.requested == []


async def test_reconcile_weekly_plan_requests_the_missed_weeks_plan_after_the_deadline() -> None:
    plan = FakePlan()
    # Monday 2026-08-03 10:00 Moscow, after the default mon 09:00 deadline.
    after_deadline = datetime(2026, 8, 3, 7, 0, tzinfo=UTC)

    result = await reconcile_weekly_plan(plan, tz=MOSCOW, now=lambda: after_deadline)

    assert plan.requested == ["2026-W32"]
    assert result == DEFAULT_JOB_ID


async def test_reconcile_weekly_plan_still_recovers_the_week_late_in_the_same_iso_week() -> None:
    plan = FakePlan()
    # Sunday 2026-08-09 is still ISO week 32 (started Monday 2026-08-03); the
    # deadline was missed days ago but this week's Plan is still recoverable.
    sunday_same_week = datetime(2026, 8, 9, 12, 0, tzinfo=UTC)

    result = await reconcile_weekly_plan(plan, tz=MOSCOW, now=lambda: sunday_same_week)

    assert plan.requested == ["2026-W32"]
    assert result == DEFAULT_JOB_ID


async def test_reconcile_weekly_plan_does_not_duplicate_an_already_existing_plan() -> None:
    plan = FakePlan(active_weeks={"2026-W32"})
    after_deadline = datetime(2026, 8, 3, 7, 0, tzinfo=UTC)

    result = await reconcile_weekly_plan(plan, tz=MOSCOW, now=lambda: after_deadline)

    assert result is None
    assert plan.requested == []


async def test_reconcile_weekly_plan_respects_a_persisted_custom_schedule() -> None:
    plan = FakePlan()
    # Wednesday 2026-08-05, before/after a custom wed 14:30 deadline (Moscow).
    before_custom_deadline = datetime(2026, 8, 5, 10, 0, tzinfo=UTC)
    after_custom_deadline = datetime(2026, 8, 5, 12, 0, tzinfo=UTC)

    before_result = await reconcile_weekly_plan(
        plan, tz=MOSCOW, day_of_week="wed", hour=14, minute=30, now=lambda: before_custom_deadline
    )
    after_result = await reconcile_weekly_plan(
        plan, tz=MOSCOW, day_of_week="wed", hour=14, minute=30, now=lambda: after_custom_deadline
    )

    assert before_result is None
    assert plan.requested == ["2026-W32"]
    assert after_result == DEFAULT_JOB_ID
