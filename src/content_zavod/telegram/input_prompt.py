"""InputPrompt: ask one user for one typed value, on top of the shared `PendingInputs` wait (#88).

The Главное меню and Экран Настроек (#95) ask for free text in several places (a new Ниша, the
time of the Расписание, a Тема to propose). Each ask is one `PendingInputs` row of the
Пространство under the caller's own `kind`: one message - the question with «Отмена» - and the
user's next text in the private chat is the answer.

Whatever closes a wait - an answer, «Отмена», a newer ask replacing it - removes its prompt
message, so no dead question is left in the chat. A rejected answer doesn't close it: the
question is edited to say what was wrong and the same wait is stored again.
"""

from __future__ import annotations

from collections.abc import Sequence

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from ..workspaces import WorkspaceId
from .callback_codec import SimpleAction, encode_callback_data
from .comment_gated_regeneration import PendingInputStore
from .gateway import BotClient
from .pending_inputs import PendingInput
from .texts import CANCEL_BUTTON, INPUT_HINT, SETTINGS_INVALID

ButtonRows = Sequence[Sequence[InlineKeyboardButton]]


def build_cancel_input_keyboard(kind: str, buttons: ButtonRows = ()) -> InlineKeyboardMarkup:
    """«Отмена», under the caller's own `buttons` rows if any (the onboarding wizard's
    «◀ Назад» / «Пропустить», #96)."""
    cancel = InlineKeyboardButton(
        text=CANCEL_BUTTON,
        callback_data=encode_callback_data(SimpleAction("cancel_input", kind)),
    )
    return InlineKeyboardMarkup(inline_keyboard=[*(list(row) for row in buttons), [cancel]])


def question_text(question: str) -> str:
    return f"{question}\n\n{INPUT_HINT}"


class InputPrompt:
    def __init__(self, bot: BotClient, pending: PendingInputStore) -> None:
        self._bot = bot
        self._pending = pending

    async def ask(
        self,
        workspace_id: WorkspaceId,
        chat_id: int,
        user_id: int,
        kind: str,
        target_id: str,
        question: str,
        *,
        buttons: ButtonRows = (),
    ) -> None:
        """Send `question` and wait for this user's answer under `kind`/`target_id`; an
        earlier wait of this user in this chat, of any kind, is dropped with its prompt."""
        prompt_message_id = await self._bot.send_message(
            chat_id,
            question_text(question),
            reply_markup=build_cancel_input_keyboard(kind, buttons),
        )
        replaced = await self._pending.put(
            workspace_id, chat_id, user_id, PendingInput(kind, target_id, prompt_message_id)
        )
        if replaced is not None:
            await self.close(chat_id, replaced)

    async def take(
        self, workspace_id: WorkspaceId, chat_id: int, user_id: int, kind: str
    ) -> PendingInput | None:
        """The live `kind` wait a text answers, removed."""
        return await self._pending.take(workspace_id, chat_id, user_id, kind)

    async def ask_again(
        self,
        workspace_id: WorkspaceId,
        chat_id: int,
        user_id: int,
        pending: PendingInput,
        problem: str,
        question: str,
        *,
        buttons: ButtonRows = (),
    ) -> None:
        """A taken answer was rejected: say why on the same question and wait again."""
        text = f"{SETTINGS_INVALID.format(text=problem)}\n\n{question}"
        await self._bot.edit_message_text(
            chat_id,
            pending.prompt_message_id,
            question_text(text),
            reply_markup=build_cancel_input_keyboard(pending.kind, buttons),
        )
        replaced = await self._pending.put(workspace_id, chat_id, user_id, pending)
        if replaced is not None:  # only if another ask slipped in between take and put
            await self.close(chat_id, replaced)

    async def cancel(
        self,
        workspace_id: WorkspaceId,
        chat_id: int,
        user_id: int,
        kind: str,
        *,
        message_id: int | None = None,
    ) -> bool:
        """«Отмена»: drop this user's `kind` wait, if any, and its prompt. `message_id` is the
        question the button sits on: only that wait is dropped, so a stale «Отмена» under an
        older question doesn't cancel a newer one."""
        pending = await self._pending.take(
            workspace_id, chat_id, user_id, kind, reply_to_message_id=message_id
        )
        if pending is None:
            return False
        await self.close(chat_id, pending)
        return True

    async def is_waiting(self, workspace_id: WorkspaceId, chat_id: int, user_id: int) -> bool:
        """Whether this user has a live wait in this chat, of any kind - so a message no
        handler took is still not "unsolicited" if some flow asked for it."""
        return await self._pending.get(workspace_id, chat_id, user_id) is not None

    async def close(self, chat_id: int, pending: PendingInput) -> None:
        await self._bot.delete_message(chat_id, pending.prompt_message_id)
