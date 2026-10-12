from __future__ import annotations

from content_zavod.scheduling import ScheduleConfig, ScheduleSettings
from tests.tenancy import WS_A, WS_B


async def test_get_returns_none_when_unset(schedule_settings: ScheduleSettings) -> None:
    assert await schedule_settings.get(WS_A) is None


async def test_set_then_get_roundtrips(schedule_settings: ScheduleSettings) -> None:
    await schedule_settings.set(WS_A, "tue", 10, 30)

    assert await schedule_settings.get(WS_A) == ScheduleConfig(
        day_of_week="tue", hour=10, minute=30
    )


async def test_set_is_idempotent_upsert(schedule_settings: ScheduleSettings) -> None:
    await schedule_settings.set(WS_A, "tue", 10, 30)
    await schedule_settings.set(WS_A, "fri", 9, 0)

    assert await schedule_settings.get(WS_A) == ScheduleConfig(day_of_week="fri", hour=9, minute=0)


async def test_schedules_are_per_workspace(schedule_settings: ScheduleSettings) -> None:
    await schedule_settings.set(WS_A, "tue", 10, 30)

    assert await schedule_settings.get(WS_B) is None
    await schedule_settings.set(WS_B, "fri", 9, 0)
    assert await schedule_settings.get(WS_A) == ScheduleConfig(
        day_of_week="tue", hour=10, minute=30
    )
