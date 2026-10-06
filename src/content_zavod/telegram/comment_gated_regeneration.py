from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Protocol, cast

from .pending_inputs import PendingInput

type RegenerateOp[Id] = Callable[[Id, str | None], Awaitable[None]]


class CommentPrompt[Id](Protocol):
    async def prompt_for_comment(self, chat_id: int, user_id: int, id_: Id) -> tuple[int, int]:
        """Sends the request and returns `(prompt_message_id, force_reply_message_id)` (#80/#88)."""
        ...

    async def mark_generating(self, chat_id: int, prompt_message_id: int) -> None: ...


class PendingInputStore(Protocol):
    """What this flow needs from `PendingInputs` (#88) - a fake in tests, Postgres in the bot."""

    async def put(self, chat_id: int, user_id: int, pending: PendingInput) -> None: ...

    async def get(self, chat_id: int, user_id: int) -> PendingInput | None: ...

    async def take(
        self,
        chat_id: int,
        user_id: int,
        kind: str,
        *,
        target_id: str | None = None,
        reply_to_message_id: int | None = None,
    ) -> PendingInput | None: ...


class CommentGatedRegeneration[Id: str]:
    """Regenerate-with-optional-comment flow, shared by any 'press to regenerate' UI.

    First press on a target prompts for a comment; a second press on the same
    target (the Skip button, or the original 🔄 again) regenerates without one;
    a press on a different target silently cancels the earlier wait. One waiting
    prompt per (chat_id, user_id), stored in `pending` under this flow's `kind`
    so it survives a bot restart (#88).

    Once a wait resolves, the prompt's own request message is edited to
    "⏳ Генерирую..." - never the message whose button was pressed, which may be
    the Plan or the Статья card itself (#80).
    """

    def __init__(
        self,
        regenerate: RegenerateOp[Id],
        prompt: CommentPrompt[Id],
        pending: PendingInputStore,
        *,
        kind: str,
    ) -> None:
        self._regenerate = regenerate
        self._prompt = prompt
        self._pending = pending
        self._kind = kind

    async def request(self, chat_id: int, user_id: int, id_: Id) -> None:
        pending = await self._pending.take(chat_id, user_id, self._kind, target_id=id_)
        if pending is not None:
            await self._regenerate(id_, None)
            await self._prompt.mark_generating(chat_id, pending.prompt_message_id)
            return
        prompt_message_id, force_reply_message_id = await self._prompt.prompt_for_comment(
            chat_id, user_id, id_
        )
        await self._pending.put(
            chat_id,
            user_id,
            PendingInput(self._kind, id_, prompt_message_id, force_reply_message_id),
        )

    async def handle_comment_reply(
        self, chat_id: int, user_id: int, text: str, reply_to_message_id: int | None
    ) -> bool:
        """`reply_to_message_id` is the message `text` replied to. In a group the caller must
        pass it, and only a reply to this wait's prompt counts (#88); `None` means the chat
        doesn't bind comments to the prompt (a private chat), so any text resolves the wait."""
        pending = await self._pending.take(
            chat_id, user_id, self._kind, reply_to_message_id=reply_to_message_id
        )
        if pending is None:
            return False
        await self._regenerate(cast(Id, pending.target_id), text)
        await self._prompt.mark_generating(chat_id, pending.prompt_message_id)
        return True

    async def cancel(self, chat_id: int, user_id: int, id_: Id | None = None) -> bool:
        """Drop this flow's wait (only if it's for `id_`, when given - so a stale Отмена button
        can't cancel a newer wait, #88). Returns whether there was one to drop."""
        return await self._pending.take(chat_id, user_id, self._kind, target_id=id_) is not None

    async def has_matching_pending(self, chat_id: int, user_id: int, id_: Id) -> bool:
        """True if `request(chat_id, user_id, id_)` would enqueue immediately rather than prompt.

        Lets a caller (e.g. the Telegram callback handler) show a "generating..."
        progress indicator only when a Job is actually about to be enqueued.
        """
        pending = await self._pending.get(chat_id, user_id)
        return pending is not None and pending.kind == self._kind and pending.target_id == id_
