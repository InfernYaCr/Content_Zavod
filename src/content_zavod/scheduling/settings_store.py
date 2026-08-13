"""ScheduleSettings: a single persisted override for the weekly plan trigger's cron schedule.

There is exactly one schedule to configure, so this is a single-row table
keyed by a fixed id rather than a generic key-value store. `get()` returning
`None` means "no override recorded yet - use the module defaults", which is
what `main()` falls back to at startup.
"""

from __future__ import annotations

from dataclasses import dataclass

import asyncpg

_ROW_ID = "weekly_plan_trigger"


@dataclass(frozen=True)
class ScheduleConfig:
    day_of_week: str
    hour: int
    minute: int


class ScheduleSettings:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def get(self) -> ScheduleConfig | None:
        row = await self._pool.fetchrow(
            "SELECT day_of_week, hour, minute FROM schedule_settings WHERE id = $1", _ROW_ID
        )
        if row is None:
            return None
        return ScheduleConfig(
            day_of_week=row["day_of_week"], hour=row["hour"], minute=row["minute"]
        )

    async def set(self, day_of_week: str, hour: int, minute: int) -> None:
        await self._pool.execute(
            """
            INSERT INTO schedule_settings (id, day_of_week, hour, minute)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (id) DO UPDATE
            SET day_of_week = EXCLUDED.day_of_week, hour = EXCLUDED.hour, minute = EXCLUDED.minute,
                updated_at = now()
            """,
            _ROW_ID,
            day_of_week,
            hour,
            minute,
        )
