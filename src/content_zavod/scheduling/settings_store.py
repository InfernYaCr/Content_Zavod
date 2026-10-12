"""ScheduleSettings: each Пространство's persisted override for the weekly plan trigger's
schedule.

One row per Workspace (ADR-0015), keyed by `workspace_id`. `get()` returning `None` means
"no override recorded yet - use the module defaults" (пн 09:00).
"""

from __future__ import annotations

from dataclasses import dataclass

import asyncpg

from ..workspaces import WorkspaceId


@dataclass(frozen=True)
class ScheduleConfig:
    day_of_week: str
    hour: int
    minute: int


class ScheduleSettings:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def get(self, workspace_id: WorkspaceId) -> ScheduleConfig | None:
        row = await self._pool.fetchrow(
            "SELECT day_of_week, hour, minute FROM schedule_settings WHERE workspace_id = $1",
            workspace_id,
        )
        if row is None:
            return None
        return ScheduleConfig(
            day_of_week=row["day_of_week"], hour=row["hour"], minute=row["minute"]
        )

    async def set(
        self, workspace_id: WorkspaceId, day_of_week: str, hour: int, minute: int
    ) -> None:
        await self._pool.execute(
            """
            INSERT INTO schedule_settings (workspace_id, day_of_week, hour, minute)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (workspace_id) DO UPDATE
            SET day_of_week = EXCLUDED.day_of_week, hour = EXCLUDED.hour, minute = EXCLUDED.minute,
                updated_at = now()
            """,
            workspace_id,
            day_of_week,
            hour,
            minute,
        )
