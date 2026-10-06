"""Membership: a Postgres-backed Telegram-id allowlist with roles.

`role_for` is the only thing most callers need: `None` means "not on the
allowlist", any `Role` means "allowed, and here's what they may do". Owner
and content-manager are the two roles named in the domain vocabulary
(Владелец / Контент-менеджер) - see CONTEXT.md.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import asyncpg

from .errors import CannotRemoveSelf, LastOwnerRemoval, MemberNotFound

Role = Literal["owner", "content_manager"]


@dataclass(frozen=True)
class MemberView:
    telegram_id: int
    role: Role
    # From the member's latest approved заявка (#90); `None` for an Owner added by hand.
    username: str | None = None


class Membership:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def role_for(self, telegram_id: int) -> Role | None:
        row = await self._pool.fetchrow(
            "SELECT role FROM members WHERE telegram_id = $1", telegram_id
        )
        return row["role"] if row is not None else None

    async def add_member(self, telegram_id: int, role: Role) -> None:
        await self._pool.execute(
            """
            INSERT INTO members (telegram_id, role)
            VALUES ($1, $2)
            ON CONFLICT (telegram_id) DO UPDATE SET role = EXCLUDED.role, updated_at = now()
            """,
            telegram_id,
            role,
        )

    async def list_by_role(self, role: Role) -> list[int]:
        rows = await self._pool.fetch(
            "SELECT telegram_id FROM members WHERE role = $1 ORDER BY telegram_id", role
        )
        return [row["telegram_id"] for row in rows]

    async def list_all(self) -> list[MemberView]:
        """Every member, with the @username from their latest approved заявка (#90) - no
        username column of its own, `join_requests` already has it."""
        rows = await self._pool.fetch(
            """
            SELECT m.telegram_id, m.role, jr.username
            FROM members m
            LEFT JOIN LATERAL (
                SELECT username FROM join_requests
                WHERE telegram_id = m.telegram_id AND status = 'approved'
                ORDER BY id DESC
                LIMIT 1
            ) jr ON true
            ORDER BY m.telegram_id
            """
        )
        return [
            MemberView(telegram_id=row["telegram_id"], role=row["role"], username=row["username"])
            for row in rows
        ]

    async def remove_member(self, telegram_id: int, *, removed_by: int) -> None:
        """Refuses to remove `removed_by` themselves or the last Owner - either would leave the
        bot without an administrator (#90). Every Owner row is locked first, so two Owners
        removing each other at once can't both succeed."""
        if telegram_id == removed_by:
            raise CannotRemoveSelf()
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                owners = await conn.fetch(
                    "SELECT telegram_id FROM members WHERE role = 'owner' FOR UPDATE"
                )
                if [row["telegram_id"] for row in owners] == [telegram_id]:
                    raise LastOwnerRemoval()
                result = await conn.execute(
                    "DELETE FROM members WHERE telegram_id = $1", telegram_id
                )
        if result == "DELETE 0":
            raise MemberNotFound(telegram_id)
