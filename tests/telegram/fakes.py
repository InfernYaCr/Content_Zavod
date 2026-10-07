from __future__ import annotations

from content_zavod.telegram.pending_inputs import PendingInput


class FakePendingInputs:
    """In-memory `PendingInputs` (#88): same one-row-per-(chat_id, user_id) and `take`
    matching rules as the SQL, minus the TTL (covered by tests/telegram/test_pending_inputs.py)."""

    def __init__(self) -> None:
        self.rows: dict[tuple[int, int], PendingInput] = {}

    async def put(self, chat_id: int, user_id: int, pending: PendingInput) -> PendingInput | None:
        previous = self.rows.get((chat_id, user_id))
        self.rows[(chat_id, user_id)] = pending
        return previous

    async def get(self, chat_id: int, user_id: int) -> PendingInput | None:
        return self.rows.get((chat_id, user_id))

    async def take(
        self,
        chat_id: int,
        user_id: int,
        kind: str,
        *,
        target_id: str | None = None,
        reply_to_message_id: int | None = None,
    ) -> PendingInput | None:
        pending = self.rows.get((chat_id, user_id))
        if (
            pending is None
            or pending.kind != kind
            or (target_id is not None and pending.target_id != target_id)
            or (
                reply_to_message_id is not None
                and reply_to_message_id
                not in (pending.prompt_message_id, pending.force_reply_message_id)
            )
        ):
            return None
        return self.rows.pop((chat_id, user_id))


class RecordingBot:
    """`BotClient` that records every call; message ids count up from 100."""

    def __init__(self) -> None:
        self.sent: list[tuple[int, str, object, str | None]] = []
        self.edited: list[tuple[int, int, str, object]] = []
        self.deleted: list[tuple[int, int]] = []
        self.fail_edits = False
        self._next_id = 100

    async def send_message(self, chat_id, text, reply_markup=None, parse_mode=None) -> int:
        self.sent.append((chat_id, text, reply_markup, parse_mode))
        message_id = self._next_id
        self._next_id += 1
        return message_id

    async def send_document(self, chat_id, document, caption=None) -> None:
        pass

    async def send_photo(self, chat_id, photo, caption=None) -> None:
        pass

    async def edit_message_text(self, chat_id, message_id, text, reply_markup=None) -> None:
        if self.fail_edits:
            raise RuntimeError("message to edit not found")
        self.edited.append((chat_id, message_id, text, reply_markup))

    async def edit_message_reply_markup(self, chat_id, message_id, reply_markup=None) -> None:
        pass

    async def delete_message(self, chat_id, message_id) -> None:
        self.deleted.append((chat_id, message_id))

    async def set_my_commands(self, commands, *, scope) -> None:
        pass


def button_texts(markup) -> list[list[str]]:
    return [[button.text for button in row] for row in markup.inline_keyboard]


def button_data(markup) -> list[list[str | None]]:
    return [
        [button.callback_data or button.url for button in row] for row in markup.inline_keyboard
    ]
