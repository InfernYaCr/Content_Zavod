from __future__ import annotations

from typing import Literal, Protocol

from ..workspaces import WorkspaceId
from .comment_gated_regeneration import CommentGatedRegeneration, CommentPrompt, PendingInputStore
from .types import PlanId, PlanItemId


class PlanOperations(Protocol):
    """Domain operations PlanReview delegates to (enqueued via Job Queue, see #2)."""

    async def delete_item(self, workspace_id: WorkspaceId, plan_item_id: PlanItemId) -> None: ...

    async def regenerate_item(
        self, workspace_id: WorkspaceId, plan_item_id: PlanItemId, comment: str | None
    ) -> None: ...

    async def approve_all(self, workspace_id: WorkspaceId, plan_id: PlanId) -> None: ...


class PlanReview:
    """One waiting comment prompt per (Пространство, chat_id, user_id); a new one silently
    cancels the old."""

    def __init__(
        self,
        ops: PlanOperations,
        prompt: CommentPrompt[PlanItemId],
        pending: PendingInputStore,
    ) -> None:
        self._ops = ops
        self._regeneration = CommentGatedRegeneration[PlanItemId](
            ops.regenerate_item, prompt, pending, kind="plan_item_comment"
        )

    async def handle_action(
        self,
        workspace_id: WorkspaceId,
        chat_id: int,
        user_id: int,
        id_: str,
        action: Literal["regenerate", "delete", "approve_all"],
    ) -> None:
        """`id_` is the Тема's id - the Plan's for `approve_all`."""
        if action == "regenerate":
            await self._regeneration.request(workspace_id, chat_id, user_id, PlanItemId(id_))
            return

        await self._regeneration.cancel(workspace_id, chat_id, user_id)
        if action == "delete":
            await self._ops.delete_item(workspace_id, PlanItemId(id_))
        elif action == "approve_all":
            await self._ops.approve_all(workspace_id, PlanId(id_))

    async def handle_comment_reply(
        self, workspace_id: WorkspaceId, chat_id: int, user_id: int, text: str
    ) -> bool:
        return await self._regeneration.handle_comment_reply(workspace_id, chat_id, user_id, text)

    async def cancel_comment(
        self, workspace_id: WorkspaceId, chat_id: int, user_id: int, plan_item_id: PlanItemId
    ) -> bool:
        """The comment prompt's Отмена button (#88): drop the wait for this Тема, if any."""
        return await self._regeneration.cancel(workspace_id, chat_id, user_id, plan_item_id)

    async def will_enqueue_regeneration(
        self, workspace_id: WorkspaceId, chat_id: int, user_id: int, plan_item_id: PlanItemId
    ) -> bool:
        return await self._regeneration.has_matching_pending(
            workspace_id, chat_id, user_id, plan_item_id
        )
