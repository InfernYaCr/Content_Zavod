from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable, Generic, Protocol, TypeVar

Id = TypeVar("Id")

RegenerateOp = Callable[[Id, "str | None"], Awaitable[None]]


class CommentPrompt(Protocol[Id]):
    async def prompt_for_comment(self, chat_id: int, id_: Id) -> None: ...


@dataclass(frozen=True)
class _PendingComment(Generic[Id]):
    id_: Id


class CommentGatedRegeneration(Generic[Id]):
    """Regenerate-with-optional-comment flow, shared by any 'press to regenerate' UI.

    First press on a target prompts for a comment; a second press on the same
    target (the Skip button) regenerates without one; a press on a different
    target silently cancels the earlier wait. One waiting prompt per
    (chat_id, user_id).
    """

    def __init__(self, regenerate: RegenerateOp[Id], prompt: CommentPrompt[Id]) -> None:
        self._regenerate = regenerate
        self._prompt = prompt
        self._pending: dict[tuple[int, int], _PendingComment[Id]] = {}

    async def request(self, chat_id: int, user_id: int, id_: Id) -> None:
        key = (chat_id, user_id)
        pending = self._pending.get(key)
        if pending is not None and pending.id_ == id_:
            del self._pending[key]
            await self._regenerate(id_, None)
            return
        self._pending[key] = _PendingComment(id_)
        await self._prompt.prompt_for_comment(chat_id, id_)

    async def handle_comment_reply(self, chat_id: int, user_id: int, text: str) -> bool:
        pending = self._pending.pop((chat_id, user_id), None)
        if pending is None:
            return False
        await self._regenerate(pending.id_, text)
        return True

    def cancel(self, chat_id: int, user_id: int) -> None:
        self._pending.pop((chat_id, user_id), None)

    def has_matching_pending(self, chat_id: int, user_id: int, id_: Id) -> bool:
        """True if `request(chat_id, user_id, id_)` would enqueue immediately rather than prompt.

        Lets a caller (e.g. the Telegram callback handler) show a "generating..."
        progress indicator only when a Job is actually about to be enqueued.
        """
        pending = self._pending.get((chat_id, user_id))
        return pending is not None and pending.id_ == id_
