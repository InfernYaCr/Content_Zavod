"""Key-value stores for settings: one per Пространство, one for the bot itself (ADR-0015).

`WorkspaceSettingsStore` backs the Владелец-editable Настройки (Ниша, Персона, Аудитория,
...), the Онбординг flag and the Карта Ниши (#128) of one Workspace, sharing one table keyed
by (workspace_id, name), so each new setting reuses this store rather than growing its own
table. `get()` returning `None` means "no override recorded yet" - each caller decides its
own fallback default.

`BotStateStore` is the bot's own global state with no user data in it - the Инструкция's
picture file_ids and the Telegraph account token - shared by every Workspace.
"""

from __future__ import annotations

import asyncpg

from ..workspaces import WorkspaceId


class WorkspaceSettingsStore:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def get(self, workspace_id: WorkspaceId, key: str) -> str | None:
        row = await self._pool.fetchrow(
            "SELECT value FROM workspace_settings WHERE workspace_id = $1 AND key = $2",
            workspace_id,
            key,
        )
        return row["value"] if row is not None else None

    async def set(self, workspace_id: WorkspaceId, key: str, value: str) -> None:
        await self._pool.execute(
            """
            INSERT INTO workspace_settings (workspace_id, key, value)
            VALUES ($1, $2, $3)
            ON CONFLICT (workspace_id, key) DO UPDATE
            SET value = EXCLUDED.value, updated_at = now()
            """,
            workspace_id,
            key,
            value,
        )

    async def set_if_changed(self, workspace_id: WorkspaceId, key: str, value: str) -> bool:
        """`set`, reporting whether it changed anything - atomically, so of two concurrent
        calls with the same value exactly one gets `True` (a double-tapped «🚀 Запустить»
        must start one first Plan, not two)."""
        row = await self._pool.fetchrow(
            """
            INSERT INTO workspace_settings (workspace_id, key, value)
            VALUES ($1, $2, $3)
            ON CONFLICT (workspace_id, key) DO UPDATE
            SET value = EXCLUDED.value, updated_at = now()
            WHERE workspace_settings.value IS DISTINCT FROM EXCLUDED.value
            RETURNING key
            """,
            workspace_id,
            key,
            value,
        )
        return row is not None


class BotStateStore:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def get(self, key: str) -> str | None:
        row = await self._pool.fetchrow("SELECT value FROM bot_state WHERE key = $1", key)
        return row["value"] if row is not None else None

    async def set(self, key: str, value: str) -> None:
        await self._pool.execute(
            """
            INSERT INTO bot_state (key, value)
            VALUES ($1, $2)
            ON CONFLICT (key) DO UPDATE
            SET value = EXCLUDED.value, updated_at = now()
            """,
            key,
            value,
        )
