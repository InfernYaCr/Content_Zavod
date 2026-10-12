from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from apscheduler.triggers.interval import IntervalTrigger

from content_zavod.scheduling import (
    DEFAULT_SCHEDULE,
    JOB_ID,
    ScheduleConfig,
    due_week,
    schedule_weekly_plan_tick,
    week_label_for,
    weekly_plan_tick,
)

MOSCOW = ZoneInfo("Europe/Moscow")


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


def test_due_week_is_none_before_this_weeks_deadline() -> None:
    # Monday 2026-08-03 08:00 Moscow, before the default mon 09:00 deadline.
    before_deadline = datetime(2026, 8, 3, 5, 0, tzinfo=UTC)

    assert due_week(before_deadline, MOSCOW, DEFAULT_SCHEDULE) is None


def test_due_week_is_this_week_after_the_deadline() -> None:
    after_deadline = datetime(2026, 8, 3, 7, 0, tzinfo=UTC)  # Monday 10:00 Moscow

    assert due_week(after_deadline, MOSCOW, DEFAULT_SCHEDULE) == "2026-W32"


def test_due_week_still_holds_late_in_the_same_iso_week() -> None:
    # Sunday 2026-08-09 is still ISO week 32: a deadline missed days ago is still caught up.
    sunday_same_week = datetime(2026, 8, 9, 12, 0, tzinfo=UTC)

    assert due_week(sunday_same_week, MOSCOW, DEFAULT_SCHEDULE) == "2026-W32"


def test_due_week_respects_a_custom_schedule() -> None:
    wednesday_1430 = ScheduleConfig(day_of_week="wed", hour=14, minute=30)
    # Wednesday 2026-08-05, before/after 14:30 Moscow.
    before = datetime(2026, 8, 5, 10, 0, tzinfo=UTC)
    after = datetime(2026, 8, 5, 12, 0, tzinfo=UTC)

    assert due_week(before, MOSCOW, wednesday_1430) is None
    assert due_week(after, MOSCOW, wednesday_1430) == "2026-W32"


def test_the_tick_runs_every_minute_starting_right_away() -> None:
    scheduler = FakeScheduler()
    pool, plan = object(), object()
    before = datetime.now(UTC)

    schedule_weekly_plan_tick(scheduler, pool, plan)

    [job] = scheduler.jobs
    assert job["func"] is weekly_plan_tick
    assert job["args"] == [pool, plan]
    assert isinstance(job["trigger"], IntervalTrigger)
    assert job["trigger"].interval == timedelta(minutes=1)
    # The first run catches up a deadline missed while the bot was down (#72).
    assert job["next_run_time"] >= before
    assert job["id"] == JOB_ID
    assert job["replace_existing"] is True
    assert job["coalesce"] is True
    assert job["max_instances"] == 1
