"""PendingInputs: the bot's awaited free-text replies, persisted in Postgres (#88).

One row per (workspace_id, chat_id, user_id) (ADR-0015: every method takes the Пространство
first, and a wait of one Workspace is invisible to another) - asking a user for something new
overwrites whatever they were last asked in that chat, of any `kind`. Replaces `CommentGatedRegeneration`'s in-memory
dict, so a wait survives a bot restart, and is shared by every flow that asks for typed input
(the regeneration comment, Настройки, the onboarding wizard, a proposed Тема), each under its own `kind`.

A wait older than `PENDING_INPUT_TTL` is treated as gone: `get`/`take` ignore it and the next
`put` for that (chat_id, user_id) overwrites it, so there is no cleanup job. `put` hands back
the row it overwrote (expired or not), so the caller can remove that wait's prompt messages.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

import asyncpg

from ..workspaces import WorkspaceId

PENDING_INPUT_TTL = timedelta(minutes=30)


@dataclass(frozen=True)
class PendingInput:
    kind: str
    target_id: str
    prompt_message_id: int
    force_reply_message_id: int | None = None
    """Always `None` since the bot left group chats (#118): their ForceReply line is gone. The
    column stays until a later contract migration drops it (ADR-0015)."""


class PendingInputs:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def put(
        self, workspace_id: WorkspaceId, chat_id: int, user_id: int, pending: PendingInput
    ) -> PendingInput | None:
        """Store `pending` as this (chat_id, user_id)'s wait; returns the wait it replaced, if
        any - expired ones included, since their prompt messages are still in the chat."""
        async with self._pool.acquire() as conn, conn.transaction():
            previous = await conn.fetchrow(
                """
                DELETE FROM pending_inputs
                WHERE workspace_id = $3 AND chat_id = $1 AND user_id = $2
                RETURNING kind, target_id, prompt_message_id, force_reply_message_id
                """,
                chat_id,
                user_id,
                workspace_id,
            )
            await conn.execute(
                """
                INSERT INTO pending_inputs
                    (chat_id, user_id, kind, target_id, prompt_message_id, force_reply_message_id,
                     workspace_id)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                ON CONFLICT (workspace_id, chat_id, user_id) DO UPDATE
                SET kind = EXCLUDED.kind, target_id = EXCLUDED.target_id,
                    prompt_message_id = EXCLUDED.prompt_message_id,
                    force_reply_message_id = EXCLUDED.force_reply_message_id, created_at = now()
                """,
                chat_id,
                user_id,
                pending.kind,
                pending.target_id,
                pending.prompt_message_id,
                pending.force_reply_message_id,
                workspace_id,
            )
        return None if previous is None else PendingInput(**dict(previous))

    async def get(
        self, workspace_id: WorkspaceId, chat_id: int, user_id: int
    ) -> PendingInput | None:
        row = await self._pool.fetchrow(
            """
            SELECT kind, target_id, prompt_message_id, force_reply_message_id
            FROM pending_inputs
            WHERE workspace_id = $4 AND chat_id = $1 AND user_id = $2
              AND created_at > now() - $3::interval
            """,
            chat_id,
            user_id,
            PENDING_INPUT_TTL,
            workspace_id,
        )
        return None if row is None else PendingInput(**dict(row))

    async def touch(self, workspace_id: WorkspaceId, chat_id: int, user_id: int, kind: str) -> bool:
        """Renew the live `kind` wait's TTL in place; `False` if there is none. An UPDATE,
        never an insert: a wait taken a moment ago by a concurrent update stays taken."""
        result = await self._pool.execute(
            """
            UPDATE pending_inputs SET created_at = now()
            WHERE workspace_id = $1 AND chat_id = $2 AND user_id = $3 AND kind = $4
              AND created_at > now() - $5::interval
            """,
            workspace_id,
            chat_id,
            user_id,
            kind,
            PENDING_INPUT_TTL,
        )
        return result == "UPDATE 1"

    async def take(
        self,
        workspace_id: WorkspaceId,
        chat_id: int,
        user_id: int,
        kind: str,
        *,
        target_id: str | None = None,
        reply_to_message_id: int | None = None,
    ) -> PendingInput | None:
        """Atomically remove and return the live `kind` wait, if it matches - so two updates
        racing for the same wait (a double-tapped Пропустить, two quick replies) resolve it
        once. `target_id`, when given, must match the wait's target; `reply_to_message_id`,
        when given, must be one of the wait's prompt messages."""
        row = await self._pool.fetchrow(
            """
            DELETE FROM pending_inputs
            WHERE workspace_id = $7 AND chat_id = $1 AND user_id = $2 AND kind = $3
              AND created_at > now() - $4::interval
              AND ($5::text IS NULL OR target_id = $5)
              AND ($6::bigint IS NULL OR $6 IN (prompt_message_id, force_reply_message_id))
            RETURNING kind, target_id, prompt_message_id, force_reply_message_id
            """,
            chat_id,
            user_id,
            kind,
            PENDING_INPUT_TTL,
            target_id,
            reply_to_message_id,
            workspace_id,
        )
        return None if row is None else PendingInput(**dict(row))
