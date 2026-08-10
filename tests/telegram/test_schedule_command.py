from __future__ import annotations

from zoneinfo import ZoneInfo

import pytest
from apscheduler.triggers.cron import CronTrigger

from content_zavod.scheduling import ScheduleConfig
from content_zavod.telegram.schedule_command import (
    handle_schedule_command,
    handle_set_schedule_command,
)

MOSCOW = ZoneInfo("Europe/Moscow")


class FakeSettingsStore:
    def __init__(self, config: ScheduleConfig | None = None) -> None:
        self._config = config
        self.set_calls: list[tuple[str, int, int]] = []

    async def get(self) -> ScheduleConfig | None:
        return self._config

    async def set(self, day_of_week: str, hour: int, minute: int) -> None:
        self.set_calls.append((day_of_week, hour, minute))
        self._config = ScheduleConfig(day_of_week=day_of_week, hour=hour, minute=minute)


class FakeScheduler:
    def __init__(self) -> None:
        self.rescheduled: list[tuple[str, CronTrigger]] = []

    def reschedule_job(self, job_id, *, trigger) -> None:
        self.rescheduled.append((job_id, trigger))


class FakeGateway:
    def __init__(self) -> None:
        self.sent_notices: list[tuple[int, str]] = []
        self.sent_errors: list[tuple[int, str]] = []

    async def send_notice(self, chat_id, text) -> None:
        self.sent_notices.append((chat_id, text))

    async def send_error(self, chat_id, text) -> None:
        self.sent_errors.append((chat_id, text))


@pytest.mark.asyncio
async def test_schedule_command_reports_defaults_when_unset() -> None:
    settings_store, gateway = FakeSettingsStore(None), FakeGateway()

    await handle_schedule_command(settings_store, gateway, chat_id=1)

    assert gateway.sent_notices == [(1, "Текущее расписание: mon 09:00")]


@pytest.mark.asyncio
async def test_schedule_command_reports_persisted_override() -> None:
    settings_store = FakeSettingsStore(ScheduleConfig(day_of_week="fri", hour=10, minute=30))
    gateway = FakeGateway()

    await handle_schedule_command(settings_store, gateway, chat_id=1)

    assert gateway.sent_notices == [(1, "Текущее расписание: fri 10:30")]


@pytest.mark.asyncio
async def test_set_schedule_persists_and_reschedules() -> None:
    settings_store, scheduler, gateway = FakeSettingsStore(), FakeScheduler(), FakeGateway()

    await handle_set_schedule_command(settings_store, scheduler, gateway, chat_id=1, args="tue 10:30", tz=MOSCOW)

    assert settings_store.set_calls == [("tue", 10, 30)]
    assert len(scheduler.rescheduled) == 1
    job_id, trigger = scheduler.rescheduled[0]
    assert job_id == "weekly_plan_trigger"
    assert isinstance(trigger, CronTrigger)
    assert gateway.sent_notices == [(1, "Расписание изменено: tue 10:30")]


@pytest.mark.asyncio
async def test_invalid_day_rejected_without_side_effects() -> None:
    settings_store, scheduler, gateway = FakeSettingsStore(), FakeScheduler(), FakeGateway()

    await handle_set_schedule_command(settings_store, scheduler, gateway, chat_id=1, args="funday 10:30", tz=MOSCOW)

    assert settings_store.set_calls == []
    assert scheduler.rescheduled == []
    assert len(gateway.sent_errors) == 1


@pytest.mark.asyncio
async def test_invalid_time_rejected_without_side_effects() -> None:
    settings_store, scheduler, gateway = FakeSettingsStore(), FakeScheduler(), FakeGateway()

    await handle_set_schedule_command(settings_store, scheduler, gateway, chat_id=1, args="tue 25:99", tz=MOSCOW)

    assert settings_store.set_calls == []
    assert scheduler.rescheduled == []
    assert len(gateway.sent_errors) == 1


@pytest.mark.asyncio
async def test_missing_args_rejected() -> None:
    settings_store, scheduler, gateway = FakeSettingsStore(), FakeScheduler(), FakeGateway()

    await handle_set_schedule_command(settings_store, scheduler, gateway, chat_id=1, args="tue", tz=MOSCOW)

    assert settings_store.set_calls == []
    assert len(gateway.sent_errors) == 1
