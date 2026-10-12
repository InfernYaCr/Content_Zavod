from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Protocol, cast

from ..workspaces import WorkspaceId
from .pending_inputs import PendingInput

# A comment goes into the regeneration's prompt, so it is capped (ADR-0015, «Безопасность»).
COMMENT_MAX_LENGTH = 1000

type RegenerateOp[Id] = Callable[[WorkspaceId, Id, str | None], Awaitable[None]]


class CommentPrompt[Id](Protocol):
    async def prompt_for_comment(self, chat_id: int, id_: Id) -> int:
        """Sends the request; returns its message id."""
        ...

    async def mark_generating(self, chat_id: int, pending: PendingInput) -> None:
        """The wait resolved into a regeneration: show it on the prompt message (#80)."""
        ...

    async def withdraw(self, chat_id: int, pending: PendingInput) -> None:
        """The wait closed without a regeneration (Отмена, superseded, another action):
        remove its message, so no dead prompt is left in the chat."""
        ...

    async def reject_too_long(self, chat_id: int) -> None:
        """The comment is over `COMMENT_MAX_LENGTH`: say so; the wait stays open."""
        ...


class PendingInputStore(Protocol):
    """What this flow needs from `PendingInputs` (#88) - a fake in tests, Postgres in the bot."""

    async def put(
        self, workspace_id: WorkspaceId, chat_id: int, user_id: int, pending: PendingInput
    ) -> PendingInput | None: ...

    async def get(
        self, workspace_id: WorkspaceId, chat_id: int, user_id: int
    ) -> PendingInput | None: ...

    async def take(
        self,
        workspace_id: WorkspaceId,
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
    a press on a different target silently cancels the earlier wait (and removes
    its prompt). One waiting prompt per (Пространство, chat_id, user_id), stored in `pending`
    under this flow's `kind` so it survives a bot restart (#88); the user's next text in the
    private chat is the comment.

    Once a wait resolves, the prompt's own request message is edited to
    "⏳ Генерирую..." - never the message whose button was pressed, which may be
    the Plan or the Статья card itself (#80); a wait closed any other way has
    its prompt removed.
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

    async def request(self, workspace_id: WorkspaceId, chat_id: int, user_id: int, id_: Id) -> None:
        pending = await self._pending.take(
            workspace_id, chat_id, user_id, self._kind, target_id=id_
        )
        if pending is not None:
            await self._regenerate(workspace_id, id_, None)
            await self._prompt.mark_generating(chat_id, pending)
            return
        prompt_message_id = await self._prompt.prompt_for_comment(chat_id, id_)
        replaced = await self._pending.put(
            workspace_id, chat_id, user_id, PendingInput(self._kind, id_, prompt_message_id)
        )
        if replaced is not None:
            await self._prompt.withdraw(chat_id, replaced)

    async def handle_comment_reply(
        self, workspace_id: WorkspaceId, chat_id: int, user_id: int, text: str
    ) -> bool:
        """Any text resolves this Пространство's wait, if it has one - except one over
        `COMMENT_MAX_LENGTH`: that is refused, nothing is regenerated, and the wait stays."""
        if len(text.strip()) > COMMENT_MAX_LENGTH:
            current = await self._pending.get(workspace_id, chat_id, user_id)
            if current is None or current.kind != self._kind:
                return False
            await self._prompt.reject_too_long(chat_id)
            return True
        pending = await self._pending.take(workspace_id, chat_id, user_id, self._kind)
        if pending is None:
            return False
        await self._regenerate(workspace_id, cast(Id, pending.target_id), text)
        await self._prompt.mark_generating(chat_id, pending)
        return True

    async def cancel(
        self, workspace_id: WorkspaceId, chat_id: int, user_id: int, id_: Id | None = None
    ) -> bool:
        """Drop this flow's wait (only if it's for `id_`, when given - so a stale Отмена button
        can't cancel a newer wait, #88) and remove its prompt. Returns whether there was one."""
        pending = await self._pending.take(
            workspace_id, chat_id, user_id, self._kind, target_id=id_
        )
        if pending is None:
            return False
        await self._prompt.withdraw(chat_id, pending)
        return True

    async def has_matching_pending(
        self, workspace_id: WorkspaceId, chat_id: int, user_id: int, id_: Id
    ) -> bool:
        """True if `request(..., id_)` would enqueue immediately rather than prompt.

        Lets a caller (e.g. the Telegram callback handler) show a "generating..."
        progress indicator only when a Job is actually about to be enqueued.
        """
        pending = await self._pending.get(workspace_id, chat_id, user_id)
        return pending is not None and pending.kind == self._kind and pending.target_id == id_
