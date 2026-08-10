"""handle_schedule_command / handle_set_schedule_command: Owner-only weekly-plan schedule control.

`/set_schedule <day> <HH:MM>` validates before touching anything - an invalid
day or time gets a plain error reply and no side effects, rather than a
partially-applied change. On success the override is persisted (so it
survives a process restart, see `main()`'s startup read) and the live
APScheduler job is rescheduled immediately via its stable `JOB_ID`.
"""

from __future__ import annotations

import re
from typing import Protocol
from zoneinfo import ZoneInfo

from apscheduler.triggers.cron import CronTrigger

from ..scheduling import JOB_ID, DEFAULT_DAY_OF_WEEK, DEFAULT_HOUR, DEFAULT_MINUTE, ScheduleConfig
from .gateway import TelegramGateway

_VALID_DAYS = {"mon", "tue", "wed", "thu", "fri", "sat", "sun"}
_TIME_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


class ScheduleSettingsOperations(Protocol):
    async def get(self) -> ScheduleConfig | None: ...

    async def set(self, day_of_week: str, hour: int, minute: int) -> None: ...


class SchedulerOperations(Protocol):
    def reschedule_job(self, job_id: str, *, trigger: CronTrigger) -> None: ...


async def handle_schedule_command(
    settings_store: ScheduleSettingsOperations, gateway: TelegramGateway, chat_id: int
) -> None:
    config = await settings_store.get()
    day = config.day_of_week if config else DEFAULT_DAY_OF_WEEK
    hour = config.hour if config else DEFAULT_HOUR
    minute = config.minute if config else DEFAULT_MINUTE
    await gateway.send_notice(chat_id, f"Текущее расписание: {day} {hour:02d}:{minute:02d}")


async def handle_set_schedule_command(
    settings_store: ScheduleSettingsOperations,
    scheduler: SchedulerOperations,
    gateway: TelegramGateway,
    chat_id: int,
    args: str,
    *,
    tz: ZoneInfo,
) -> None:
    parts = args.split()
    if len(parts) != 2:
        await gateway.send_error(chat_id, "Использование: /set_schedule <день> <ЧЧ:ММ>, например: mon 09:00")
        return
    day, time_text = parts
    day = day.lower()
    if day not in _VALID_DAYS:
        await gateway.send_error(chat_id, f"Неизвестный день {day!r}. Допустимые: {', '.join(sorted(_VALID_DAYS))}")
        return
    match = _TIME_RE.match(time_text)
    if not match:
        await gateway.send_error(chat_id, f"Неверный формат времени {time_text!r}. Ожидается ЧЧ:ММ")
        return
    hour, minute = int(match.group(1)), int(match.group(2))

    await settings_store.set(day, hour, minute)
    scheduler.reschedule_job(JOB_ID, trigger=CronTrigger(day_of_week=day, hour=hour, minute=minute, timezone=tz))
    await gateway.send_notice(chat_id, f"Расписание изменено: {day} {hour:02d}:{minute:02d}")
