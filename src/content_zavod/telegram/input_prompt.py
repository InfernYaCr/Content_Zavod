"""InputPrompt: ask one user for one typed value, on top of the shared `PendingInputs` wait (#88).

The Главное меню and Экран Настроек (#95) ask for free text in several places (a new Ниша, the
time of the Расписание, a Тема to propose). Each ask is one `PendingInputs` row under the
caller's own `kind`, with the same two chat shapes as the regeneration comment prompt
(`TelegramCommentPrompt`): in a private chat one message - the question with «Отмена» - and the
user's next text is the answer; in a group a second `ForceReply(selective=True)` line mentioning
the user follows, since a bot in privacy mode only sees replies to its own messages.

Whatever closes a wait - an answer, «Отмена», a newer ask replacing it - removes its prompt
messages, so no dead question is left in the chat. A rejected answer doesn't close it: the
question is edited to say what was wrong and the same wait is stored again.
"""

from __future__ import annotations

from aiogram.types import ForceReply, InlineKeyboardButton, InlineKeyboardMarkup

from .callback_codec import SimpleAction, encode_callback_data
from .comment_gated_regeneration import PendingInputStore
from .gateway import BotClient
from .pending_inputs import PendingInput
from .texts import (
    CANCEL_BUTTON,
    INPUT_FORCE_REPLY,
    INPUT_HINT_GROUP,
    INPUT_HINT_PRIVATE,
    SETTINGS_INVALID,
)

# Telegram's limit on `ForceReply.input_field_placeholder`.
_PLACEHOLDER_LIMIT = 64


def build_cancel_input_keyboard(kind: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=CANCEL_BUTTON,
                    callback_data=encode_callback_data(SimpleAction("cancel_input", kind)),
                )
            ]
        ]
    )


def _is_private(chat_id: int, user_id: int) -> bool:
    # A private chat's id is its user's id; group and channel ids are negative.
    return chat_id == user_id


def _question_text(question: str, private: bool) -> str:
    return f"{question}\n\n{INPUT_HINT_PRIVATE if private else INPUT_HINT_GROUP}"


class InputPrompt:
    def __init__(self, bot: BotClient, pending: PendingInputStore) -> None:
        self._bot = bot
        self._pending = pending

    async def ask(
        self,
        chat_id: int,
        user_id: int,
        kind: str,
        target_id: str,
        question: str,
        *,
        placeholder: str,
    ) -> None:
        """Send `question` and wait for this user's answer under `kind`/`target_id`; an
        earlier wait of this user in this chat, of any kind, is dropped with its prompt."""
        private = _is_private(chat_id, user_id)
        prompt_message_id = await self._bot.send_message(
            chat_id,
            _question_text(question, private),
            reply_markup=build_cancel_input_keyboard(kind),
        )
        force_reply_message_id = None
        if not private:
            force_reply_message_id = await self._bot.send_message(
                chat_id,
                INPUT_FORCE_REPLY.format(user_id=user_id),
                reply_markup=ForceReply(
                    selective=True, input_field_placeholder=placeholder[:_PLACEHOLDER_LIMIT]
                ),
                parse_mode="HTML",
            )
        replaced = await self._pending.put(
            chat_id,
            user_id,
            PendingInput(kind, target_id, prompt_message_id, force_reply_message_id),
        )
        if replaced is not None:
            await self.close(chat_id, replaced)

    async def take(
        self, chat_id: int, user_id: int, kind: str, reply_to_message_id: int | None
    ) -> PendingInput | None:
        """The live `kind` wait this message answers, removed - `reply_to_message_id` is what
        the message replied to in a group, `None` in a private chat (any text answers)."""
        return await self._pending.take(
            chat_id, user_id, kind, reply_to_message_id=reply_to_message_id
        )

    async def ask_again(
        self, chat_id: int, user_id: int, pending: PendingInput, problem: str, question: str
    ) -> None:
        """A taken answer was rejected: say why on the same question and wait again."""
        text = f"{SETTINGS_INVALID.format(text=problem)}\n\n{question}"
        await self._bot.edit_message_text(
            chat_id,
            pending.prompt_message_id,
            _question_text(text, _is_private(chat_id, user_id)),
            reply_markup=build_cancel_input_keyboard(pending.kind),
        )
        replaced = await self._pending.put(chat_id, user_id, pending)
        if replaced is not None:  # only if another ask slipped in between take and put
            await self.close(chat_id, replaced)

    async def cancel(self, chat_id: int, user_id: int, kind: str) -> bool:
        """«Отмена»: drop this user's `kind` wait, if any, and its prompt."""
        pending = await self._pending.take(chat_id, user_id, kind)
        if pending is None:
            return False
        await self.close(chat_id, pending)
        return True

    async def close(self, chat_id: int, pending: PendingInput) -> None:
        await self._bot.delete_message(chat_id, pending.prompt_message_id)
        if pending.force_reply_message_id is not None:
            await self._bot.delete_message(chat_id, pending.force_reply_message_id)
