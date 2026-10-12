"""Workspaces: the registry of Пространства (#118, ADR-0015).

One Workspace per telegram id (`owner_telegram_id UNIQUE`). Every repository holding user
data takes a `WorkspaceId` as the first parameter of each method; this module is where that
id comes from - the bot resolves it from `from_user.id` of an update, the worker from the
claimed Job. It is never taken from `callback_data`, text or a Job payload.

`workspaces` itself has no `workspace_id` column: it is the tenant list, so its queries are
by `id` or by owner and are not subject to `tests/test_tenant_sql.py`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import NewType

import asyncpg

WorkspaceId = NewType("WorkspaceId", int)

_COLUMNS = (
    "id, owner_telegram_id, owner_username, timezone, plan_limit, article_limit, "
    "bot_blocked_at IS NOT NULL AS blocked"
)


@dataclass(frozen=True)
class Workspace:
    id: WorkspaceId
    owner_telegram_id: int
    owner_username: str | None
    timezone: str
    # `None` = the trial Лимит from config applies.
    plan_limit: int | None
    article_limit: int | None
    # Telegram answered 403 to a delivery and no /start came since (`bot_blocked_at`).
    blocked: bool = False


class Workspaces:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def get_or_create(
        self, owner_telegram_id: int, *, timezone: str, owner_username: str | None = None
    ) -> Workspace:
        """`/start`: the owner's Workspace, created on first call. Race-safe (one upsert): two
        concurrent `/start`s get the same row. `timezone` only applies to a new Workspace;
        a known `owner_username` is refreshed, and a `bot_blocked_at` mark is cleared - whoever
        presses /start has the bot unblocked again (ADR-0015)."""
        row = await self._pool.fetchrow(
            f"""
            INSERT INTO workspaces (owner_telegram_id, owner_username, timezone)
            VALUES ($1, $2, $3)
            ON CONFLICT (owner_telegram_id) DO UPDATE
            SET owner_username = COALESCE(EXCLUDED.owner_username, workspaces.owner_username),
                bot_blocked_at = NULL, updated_at = now()
            RETURNING {_COLUMNS}
            """,
            owner_telegram_id,
            owner_username,
            timezone,
        )
        assert row is not None
        return _to_workspace(row)

    async def get(self, workspace_id: WorkspaceId) -> Workspace | None:
        row = await self._pool.fetchrow(
            f"SELECT {_COLUMNS} FROM workspaces WHERE id = $1", workspace_id
        )
        return _to_workspace(row) if row is not None else None

    async def get_by_owner(self, owner_telegram_id: int) -> Workspace | None:
        row = await self._pool.fetchrow(
            f"SELECT {_COLUMNS} FROM workspaces WHERE owner_telegram_id = $1", owner_telegram_id
        )
        return _to_workspace(row) if row is not None else None

    async def count_created_last_day(self) -> int:
        """New Пространства in the last 24 hours - what `SIGNUP_DAILY_LIMIT` caps (ADR-0015)."""
        count = await self._pool.fetchval(
            "SELECT count(*) FROM workspaces WHERE created_at > now() - interval '24 hours'"
        )
        return int(count)

    async def set_timezone(self, workspace_id: WorkspaceId, timezone: str) -> None:
        """The Расписание's time zone (an IANA name the screen picked from its fixed list)."""
        await self._pool.execute(
            "UPDATE workspaces SET timezone = $2, updated_at = now() WHERE id = $1",
            workspace_id,
            timezone,
        )

    async def mark_blocked(self, workspace_id: WorkspaceId) -> None:
        """Telegram answered 403 to a delivery: the owner blocked the bot. The Расписание tick
        skips the Workspace until its next /start (ADR-0015)."""
        await self._pool.execute(
            "UPDATE workspaces SET bot_blocked_at = now(), updated_at = now() WHERE id = $1",
            workspace_id,
        )


def _to_workspace(row: asyncpg.Record) -> Workspace:
    return Workspace(
        id=WorkspaceId(row["id"]),
        owner_telegram_id=row["owner_telegram_id"],
        owner_username=row["owner_username"],
        timezone=row["timezone"],
        plan_limit=row["plan_limit"],
        article_limit=row["article_limit"],
        blocked=row["blocked"],
    )
