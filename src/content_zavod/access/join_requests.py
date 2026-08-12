"""JoinRequests: заявки на доступ from unregistered users, broadcast to every Owner.

A request is resolved at most once (`resolve` is idempotent - the second
Owner to tap a button gets the already-resolved row back rather than a
double-grant). `join_request_broadcasts` records one row per Owner the
request was sent to, so the requester's copy in every other Owner's chat
can be found and edited to "handled by X" once the first Owner responds.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import asyncpg

from .errors import JoinRequestNotFound

_SCHEMA_SQL = (Path(__file__).parent / "schema.sql").read_text(encoding="utf-8")

JoinRequestStatus = Literal["pending", "approved", "declined"]


@dataclass(frozen=True)
class JoinRequestView:
    id: int
    telegram_id: int
    username: str | None
    status: JoinRequestStatus
    resolved_by: int | None
    resolved_now: bool = False


@dataclass(frozen=True)
class JoinRequestBroadcast:
    owner_telegram_id: int
    chat_id: int
    message_id: int


class JoinRequests:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def ensure_schema(self) -> None:
        await self._pool.execute(_SCHEMA_SQL)

    async def create(self, telegram_id: int, username: str | None) -> int:
        row = await self._pool.fetchrow(
            "INSERT INTO join_requests (telegram_id, username) VALUES ($1, $2) RETURNING id",
            telegram_id,
            username,
        )
        return row["id"]

    async def get(self, join_request_id: int) -> JoinRequestView:
        row = await self._pool.fetchrow(
            "SELECT id, telegram_id, username, status, resolved_by FROM join_requests WHERE id = $1",
            join_request_id,
        )
        if row is None:
            raise JoinRequestNotFound(join_request_id)
        return _to_view(row)

    async def record_broadcast(self, join_request_id: int, owner_telegram_id: int, chat_id: int, message_id: int) -> None:
        await self._pool.execute(
            """
            INSERT INTO join_request_broadcasts (join_request_id, owner_telegram_id, chat_id, message_id)
            VALUES ($1, $2, $3, $4)
            """,
            join_request_id,
            owner_telegram_id,
            chat_id,
            message_id,
        )

    async def broadcasts_for(self, join_request_id: int) -> list[JoinRequestBroadcast]:
        rows = await self._pool.fetch(
            """
            SELECT owner_telegram_id, chat_id, message_id
            FROM join_request_broadcasts WHERE join_request_id = $1
            """,
            join_request_id,
        )
        return [
            JoinRequestBroadcast(
                owner_telegram_id=row["owner_telegram_id"],
                chat_id=row["chat_id"],
                message_id=row["message_id"],
            )
            for row in rows
        ]

    async def resolve(self, join_request_id: int, *, approved: bool, resolved_by: int) -> JoinRequestView:
        status: JoinRequestStatus = "approved" if approved else "declined"
        updated = await self._pool.fetchrow(
            """
            UPDATE join_requests
            SET status = $2, resolved_by = $3, updated_at = now()
            WHERE id = $1 AND status = 'pending'
            RETURNING id, telegram_id, username, status, resolved_by
            """,
            join_request_id,
            status,
            resolved_by,
        )
        if updated is not None:
            return _to_view(updated, resolved_now=True)
        row = await self._pool.fetchrow(
            "SELECT id, telegram_id, username, status, resolved_by FROM join_requests WHERE id = $1",
            join_request_id,
        )
        if row is None:
            raise JoinRequestNotFound(join_request_id)
        return _to_view(row)


def _to_view(row: asyncpg.Record, *, resolved_now: bool = False) -> JoinRequestView:
    return JoinRequestView(
        id=row["id"],
        telegram_id=row["telegram_id"],
        username=row["username"],
        status=row["status"],
        resolved_by=row["resolved_by"],
        resolved_now=resolved_now,
    )
