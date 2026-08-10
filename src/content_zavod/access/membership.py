"""Membership: a Postgres-backed Telegram-id allowlist with roles.

`role_for` is the only thing most callers need: `None` means "not on the
allowlist", any `Role` means "allowed, and here's what they may do". Owner
and content-manager are the two roles named in the domain vocabulary
(Владелец / Контент-менеджер) - see CONTEXT.md.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import asyncpg

from .errors import MemberNotFound

_SCHEMA_SQL = (Path(__file__).parent / "schema.sql").read_text(encoding="utf-8")

Role = Literal["owner", "content_manager"]


@dataclass(frozen=True)
class MemberView:
    telegram_id: int
    role: Role


class Membership:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def ensure_schema(self) -> None:
        await self._pool.execute(_SCHEMA_SQL)

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
        rows = await self._pool.fetch("SELECT telegram_id, role FROM members ORDER BY telegram_id")
        return [MemberView(telegram_id=row["telegram_id"], role=row["role"]) for row in rows]

    async def remove_member(self, telegram_id: int) -> None:
        result = await self._pool.execute("DELETE FROM members WHERE telegram_id = $1", telegram_id)
        if result == "DELETE 0":
            raise MemberNotFound(telegram_id)
