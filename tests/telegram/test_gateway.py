import pytest
from aiogram.types import BufferedInputFile, InlineKeyboardMarkup

from content_zavod.telegram import (
    ArticleView,
    PlanId,
    PlanItemId,
    PlanItemView,
    PlanView,
    TelegramCommentPrompt,
    TelegramGateway,
    decode_callback_data,
    encode_callback_data,
)
from content_zavod.telegram.gateway import CALLBACK_DATA_LIMIT, MESSAGE_LIMIT, chunk_text


class FakeBot:
    def __init__(self) -> None:
        self.sent_messages: list[tuple[int, str, InlineKeyboardMarkup | None]] = []
        self.sent_documents: list[tuple[int, BufferedInputFile, str | None]] = []

    async def send_message(self, chat_id, text, reply_markup=None) -> None:
        self.sent_messages.append((chat_id, text, reply_markup))

    async def send_document(self, chat_id, document, caption=None) -> None:
        self.sent_documents.append((chat_id, document, caption))


def make_plan(item_count: int = 2) -> PlanView:
    items = [
        PlanItemView(id=PlanItemId(f"item-{i}"), title=f"Тема {i}", status="draft")
        for i in range(item_count)
    ]
    return PlanView(id=PlanId("plan-1"), week_label="2026-W32", items=items)


@pytest.mark.asyncio
async def test_send_plan_sends_single_message_with_keyboard() -> None:
    bot = FakeBot()
    gateway = TelegramGateway(bot)

    await gateway.send_plan(chat_id=123, plan=make_plan())

    assert len(bot.sent_messages) == 1
    chat_id, text, keyboard = bot.sent_messages[0]
    assert chat_id == 123
    assert "Тема 0" in text and "Тема 1" in text
    assert keyboard is not None
    # 2 item rows (regenerate + delete) + 1 approve_all row
    assert len(keyboard.inline_keyboard) == 3


@pytest.mark.asyncio
async def test_send_plan_keyboard_callback_data_round_trips() -> None:
    bot = FakeBot()
    gateway = TelegramGateway(bot)
    plan = make_plan(1)

    await gateway.send_plan(chat_id=1, plan=plan)

    _, _, keyboard = bot.sent_messages[0]
    regenerate_button, delete_button = keyboard.inline_keyboard[0]
    assert decode_callback_data(regenerate_button.callback_data) == ("regenerate", "item-0")
    assert decode_callback_data(delete_button.callback_data) == ("delete", "item-0")
    (approve_button,) = keyboard.inline_keyboard[1]
    assert decode_callback_data(approve_button.callback_data) == ("approve_all", "plan-1")


@pytest.mark.asyncio
async def test_send_plan_chunks_long_text_and_attaches_keyboard_to_last_chunk() -> None:
    bot = FakeBot()
    gateway = TelegramGateway(bot)
    plan = make_plan(item_count=400)  # forces text past MESSAGE_LIMIT

    await gateway.send_plan(chat_id=1, plan=plan)

    assert len(bot.sent_messages) > 1
    for _, text, _ in bot.sent_messages:
        assert len(text) <= MESSAGE_LIMIT
    *earlier, last = bot.sent_messages
    assert all(keyboard is None for _, _, keyboard in earlier)
    assert last[2] is not None


@pytest.mark.asyncio
async def test_send_article_ready_sends_document_with_filename_and_caption() -> None:
    bot = FakeBot()
    gateway = TelegramGateway(bot)
    article = ArticleView(
        title="Как выбрать нишу",
        platform="Дзен",
        filename="article.docx",
        content=b"fake docx bytes",
    )

    await gateway.send_article_ready(chat_id=42, article=article)

    assert len(bot.sent_documents) == 1
    chat_id, document, caption = bot.sent_documents[0]
    assert chat_id == 42
    assert isinstance(document, BufferedInputFile)
    assert document.filename == "article.docx"
    assert document.data == b"fake docx bytes"
    assert "Как выбрать нишу" in caption
    assert "Дзен" in caption


@pytest.mark.asyncio
async def test_send_error_chunks_long_text() -> None:
    bot = FakeBot()
    gateway = TelegramGateway(bot)
    long_text = "\n".join(f"line {i}" for i in range(1000))

    await gateway.send_error(chat_id=1, text=long_text)

    assert len(bot.sent_messages) > 1
    for _, text, keyboard in bot.sent_messages:
        assert len(text) <= MESSAGE_LIMIT
        assert keyboard is None


def test_chunk_text_returns_single_chunk_when_under_limit() -> None:
    assert chunk_text("hello") == ["hello"]


def test_chunk_text_splits_on_newline_boundary() -> None:
    text = ("a" * 4093 + "\n") + "tail"
    chunks = chunk_text(text)
    assert len(chunks) == 2
    assert chunks[0] == "a" * 4093
    assert chunks[1] == "tail"


def test_encode_callback_data_round_trip() -> None:
    data = encode_callback_data("regenerate", "abc123")
    assert decode_callback_data(data) == ("regenerate", "abc123")


def test_encode_callback_data_rejects_oversized_payload() -> None:
    huge_id = "x" * CALLBACK_DATA_LIMIT
    with pytest.raises(ValueError):
        encode_callback_data("regenerate", huge_id)


def test_decode_callback_data_rejects_unknown_code() -> None:
    with pytest.raises(ValueError):
        decode_callback_data("z:abc")


@pytest.mark.asyncio
async def test_comment_prompt_sends_message_with_skip_button_encoding_regenerate() -> None:
    bot = FakeBot()
    prompt = TelegramCommentPrompt(bot)

    await prompt.prompt_for_comment(chat_id=1, plan_item_id="item-1")

    assert len(bot.sent_messages) == 1
    chat_id, _, keyboard = bot.sent_messages[0]
    assert chat_id == 1
    (skip_button,) = keyboard.inline_keyboard[0]
    assert decode_callback_data(skip_button.callback_data) == ("regenerate", "item-1")
