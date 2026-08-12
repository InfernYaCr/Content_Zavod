"""OwnerSettingsStore: a generic key-value store for Owner-editable settings.

Unlike `ScheduleSettings` (one fixed row), this backs several independent
settings (Ниша, Персона, Направления, ...) sharing one table keyed by name, so
each new setting reuses this store rather than growing its own single-row
table. `get()` returning `None` means "no override recorded yet" - each
caller decides its own fallback default.
"""

from __future__ import annotations

from pathlib import Path

import asyncpg

_SCHEMA_SQL = (Path(__file__).parent / "schema.sql").read_text(encoding="utf-8")


class OwnerSettingsStore:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def ensure_schema(self) -> None:
        await self._pool.execute(_SCHEMA_SQL)

    async def get(self, key: str) -> str | None:
        row = await self._pool.fetchrow("SELECT value FROM owner_settings WHERE key = $1", key)
        return row["value"] if row is not None else None

    async def set(self, key: str, value: str) -> None:
        await self._pool.execute(
            """
            INSERT INTO owner_settings (key, value)
            VALUES ($1, $2)
            ON CONFLICT (key) DO UPDATE
            SET value = EXCLUDED.value, updated_at = now()
            """,
            key,
            value,
        )
