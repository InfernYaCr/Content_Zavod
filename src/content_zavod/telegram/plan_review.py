from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .gateway import Action
from .types import PlanItemId


class PlanOperations(Protocol):
    """Domain operations PlanReview delegates to (enqueued via Job Queue, see #2)."""

    async def delete_item(self, plan_item_id: PlanItemId) -> None: ...

    async def regenerate_item(self, plan_item_id: PlanItemId, comment: str | None) -> None: ...

    async def approve_all(self, plan_item_id: PlanItemId) -> None: ...


class CommentPrompt(Protocol):
    async def prompt_for_comment(self, chat_id: int, plan_item_id: PlanItemId) -> None: ...


@dataclass(frozen=True)
class _PendingComment:
    plan_item_id: PlanItemId


class PlanReview:
    """One waiting comment prompt per (chat_id, user_id); a new one silently cancels the old."""

    def __init__(self, ops: PlanOperations, prompt: CommentPrompt) -> None:
        self._ops = ops
        self._prompt = prompt
        self._pending: dict[tuple[int, int], _PendingComment] = {}

    async def handle_action(
        self,
        chat_id: int,
        user_id: int,
        plan_item_id: PlanItemId,
        action: Action,
    ) -> None:
        key = (chat_id, user_id)
        if action == "regenerate":
            pending = self._pending.get(key)
            if pending is not None and pending.plan_item_id == plan_item_id:
                del self._pending[key]
                await self._ops.regenerate_item(plan_item_id, comment=None)
                return
            self._pending[key] = _PendingComment(plan_item_id)
            await self._prompt.prompt_for_comment(chat_id, plan_item_id)
            return

        self._pending.pop(key, None)
        if action == "delete":
            await self._ops.delete_item(plan_item_id)
        elif action == "approve_all":
            await self._ops.approve_all(plan_item_id)

    async def handle_comment_reply(self, chat_id: int, user_id: int, text: str) -> bool:
        pending = self._pending.pop((chat_id, user_id), None)
        if pending is None:
            return False
        await self._ops.regenerate_item(pending.plan_item_id, comment=text)
        return True
