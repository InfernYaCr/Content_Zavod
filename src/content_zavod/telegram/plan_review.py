from __future__ import annotations

from typing import Literal, Protocol

from .comment_gated_regeneration import CommentGatedRegeneration, CommentPrompt
from .types import PlanItemId


class PlanOperations(Protocol):
    """Domain operations PlanReview delegates to (enqueued via Job Queue, see #2)."""

    async def delete_item(self, plan_item_id: PlanItemId) -> None: ...

    async def regenerate_item(self, plan_item_id: PlanItemId, comment: str | None) -> None: ...

    async def approve_all(self, plan_item_id: PlanItemId) -> None: ...


class PlanReview:
    """One waiting comment prompt per (chat_id, user_id); a new one silently cancels the old."""

    def __init__(self, ops: PlanOperations, prompt: CommentPrompt[PlanItemId]) -> None:
        self._ops = ops
        self._regeneration = CommentGatedRegeneration[PlanItemId](ops.regenerate_item, prompt)

    async def handle_action(
        self,
        chat_id: int,
        user_id: int,
        plan_item_id: PlanItemId,
        action: Literal["regenerate", "delete", "approve_all"],
    ) -> None:
        if action == "regenerate":
            await self._regeneration.request(chat_id, user_id, plan_item_id)
            return

        self._regeneration.cancel(chat_id, user_id)
        if action == "delete":
            await self._ops.delete_item(plan_item_id)
        elif action == "approve_all":
            await self._ops.approve_all(plan_item_id)

    async def handle_comment_reply(self, chat_id: int, user_id: int, text: str) -> bool:
        return await self._regeneration.handle_comment_reply(chat_id, user_id, text)

    def will_enqueue_regeneration(
        self, chat_id: int, user_id: int, plan_item_id: PlanItemId
    ) -> bool:
        return self._regeneration.has_matching_pending(chat_id, user_id, plan_item_id)
