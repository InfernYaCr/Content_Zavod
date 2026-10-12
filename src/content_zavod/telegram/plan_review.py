from __future__ import annotations

from typing import Literal, Protocol

from ..workspaces import WorkspaceId
from .comment_gated_regeneration import (
    CommentGatedRegeneration,
    CommentPrompt,
    PendingInputStore,
    RegenerateOp,
)
from .types import PlanItemId


class PlanOperations(Protocol):
    """Domain operations PlanReview delegates to (enqueued via Job Queue, see #2)."""

    async def delete_item(self, workspace_id: WorkspaceId, plan_item_id: PlanItemId) -> None: ...

    async def regenerate_item(
        self, workspace_id: WorkspaceId, plan_item_id: PlanItemId, comment: str | None
    ) -> None: ...


class PlanReview:
    """One waiting comment prompt per (Пространство, chat_id, user_id); a new one silently
    cancels the old."""

    def __init__(
        self,
        ops: PlanOperations,
        prompt: CommentPrompt[PlanItemId],
        pending: PendingInputStore,
        *,
        regenerate: RegenerateOp[PlanItemId] | None = None,
    ) -> None:
        """`regenerate` - `ops.regenerate_item` wrapped in a `Limits.reserve` (the bot)."""
        self._ops = ops
        self._regeneration = CommentGatedRegeneration[PlanItemId](
            regenerate or ops.regenerate_item, prompt, pending, kind="plan_item_comment"
        )

    async def handle_action(
        self,
        workspace_id: WorkspaceId,
        chat_id: int,
        user_id: int,
        id_: str,
        action: Literal["regenerate", "delete"],
    ) -> None:
        """`id_` is the Тема's id. «Утвердить всё» is `approve_and_generate` in the callback
        dispatcher: one transaction with the Лимит check (ADR-0015)."""
        if action == "regenerate":
            await self._regeneration.request(workspace_id, chat_id, user_id, PlanItemId(id_))
            return

        await self._regeneration.cancel(workspace_id, chat_id, user_id)
        await self._ops.delete_item(workspace_id, PlanItemId(id_))

    async def handle_comment_reply(
        self, workspace_id: WorkspaceId, chat_id: int, user_id: int, text: str
    ) -> bool:
        return await self._regeneration.handle_comment_reply(workspace_id, chat_id, user_id, text)

    async def cancel_comment(
        self,
        workspace_id: WorkspaceId,
        chat_id: int,
        user_id: int,
        plan_item_id: PlanItemId | None = None,
    ) -> bool:
        """The comment prompt's Отмена button (#88): drop the wait for this Тема, if any - for
        any Тема without `plan_item_id` («Утвердить всё» leaves none to comment on)."""
        return await self._regeneration.cancel(workspace_id, chat_id, user_id, plan_item_id)

    async def will_enqueue_regeneration(
        self, workspace_id: WorkspaceId, chat_id: int, user_id: int, plan_item_id: PlanItemId
    ) -> bool:
        return await self._regeneration.has_matching_pending(
            workspace_id, chat_id, user_id, plan_item_id
        )
