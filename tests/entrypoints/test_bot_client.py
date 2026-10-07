"""`_AiogramBotClient` edit error mapping (#106): a gone message is `MessageGone`, an
unchanged redraw is success, anything else stays an aiogram error."""

from __future__ import annotations

import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import EditMessageText

from content_zavod.entrypoints.bot import _AiogramBotClient
from content_zavod.telegram import MessageGone


class RaisingBot:
    def __init__(self, description: str) -> None:
        self.description = description

    def _error(self) -> TelegramBadRequest:
        method = EditMessageText(text="x", chat_id=1, message_id=2)
        return TelegramBadRequest(method=method, message=self.description)

    async def edit_message_text(self, *args, **kwargs) -> None:
        raise self._error()

    async def edit_message_reply_markup(self, *args, **kwargs) -> None:
        raise self._error()


@pytest.mark.parametrize(
    "description",
    ["Bad Request: message to edit not found", "Bad Request: message can't be edited"],
)
async def test_a_gone_message_is_message_gone(description: str) -> None:
    client = _AiogramBotClient(RaisingBot(description))  # type: ignore[arg-type]

    with pytest.raises(MessageGone) as caught:
        await client.edit_message_text(1, 2, "text")
    assert (caught.value.chat_id, caught.value.message_id) == (1, 2)
    with pytest.raises(MessageGone):
        await client.edit_message_reply_markup(1, 2)


async def test_not_modified_is_still_success() -> None:
    client = _AiogramBotClient(
        RaisingBot(  # type: ignore[arg-type]
            "Bad Request: message is not modified: specified new message content and reply "
            "markup are exactly the same as a current content and reply markup of the message"
        )
    )

    await client.edit_message_text(1, 2, "text")
    await client.edit_message_reply_markup(1, 2)


async def test_other_bad_requests_are_raised_as_is() -> None:
    client = _AiogramBotClient(RaisingBot("Bad Request: chat not found"))  # type: ignore[arg-type]

    with pytest.raises(TelegramBadRequest):
        await client.edit_message_text(1, 2, "text")
