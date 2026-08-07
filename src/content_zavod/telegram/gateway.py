from __future__ import annotations

from typing import Literal, Protocol

from aiogram.types import (
    BufferedInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)

from .types import ArticleView, PlanView

MESSAGE_LIMIT = 4096
CALLBACK_DATA_LIMIT = 64

Action = Literal["delete", "regenerate", "approve_all", "approve"]

_ACTION_CODES: dict[Action, str] = {
    "delete": "d",
    "regenerate": "r",
    "approve_all": "a",
    "approve": "p",
}
_CODE_ACTIONS: dict[str, Action] = {code: action for action, code in _ACTION_CODES.items()}


def encode_callback_data(action: Action, id_: str) -> str:
    data = f"{_ACTION_CODES[action]}:{id_}"
    if len(data.encode("utf-8")) > CALLBACK_DATA_LIMIT:
        raise ValueError(f"callback_data exceeds {CALLBACK_DATA_LIMIT} bytes: {data!r}")
    return data


def decode_callback_data(data: str) -> tuple[Action, str]:
    code, separator, id_ = data.partition(":")
    if not separator or code not in _CODE_ACTIONS:
        raise ValueError(f"unrecognized callback_data: {data!r}")
    return _CODE_ACTIONS[code], id_


def chunk_text(text: str, limit: int = MESSAGE_LIMIT) -> list[str]:
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    remaining = text
    while len(remaining) > limit:
        split_at = remaining.rfind("\n", 0, limit)
        if split_at <= 0:
            split_at = limit
        chunks.append(remaining[:split_at])
        remaining = remaining[split_at:].lstrip("\n")
    if remaining:
        chunks.append(remaining)
    return chunks


def render_plan_text(plan: PlanView) -> str:
    lines = [f"📋 План: {plan.week_label}", ""]
    for index, item in enumerate(plan.items, start=1):
        lines.append(f"{index}. {item.title} — {item.status}")
    return "\n".join(lines)


def build_plan_keyboard(plan: PlanView) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for item in plan.items:
        rows.append(
            [
                InlineKeyboardButton(
                    text="🔄 Перегенерировать",
                    callback_data=encode_callback_data("regenerate", item.id),
                ),
                InlineKeyboardButton(
                    text="❌ Удалить",
                    callback_data=encode_callback_data("delete", item.id),
                ),
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text="✅ Утвердить всё",
                callback_data=encode_callback_data("approve_all", plan.id),
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_skip_keyboard(id_: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Пропустить",
                    callback_data=encode_callback_data("regenerate", id_),
                )
            ]
        ]
    )


def build_article_keyboard(article_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔄 Перегенерировать",
                    callback_data=encode_callback_data("regenerate", article_id),
                ),
                InlineKeyboardButton(
                    text="✅",
                    callback_data=encode_callback_data("approve", article_id),
                ),
            ]
        ]
    )


class BotClient(Protocol):
    async def send_message(
        self,
        chat_id: int,
        text: str,
        reply_markup: InlineKeyboardMarkup | None = None,
    ) -> None: ...

    async def send_document(
        self,
        chat_id: int,
        document: BufferedInputFile,
        caption: str | None = None,
    ) -> None: ...


class TelegramGateway:
    """Thin adapter over a Telegram bot client: rendering, chunking, keyboards only."""

    def __init__(self, bot: BotClient) -> None:
        self._bot = bot

    async def send_plan(self, chat_id: int, plan: PlanView) -> None:
        chunks = chunk_text(render_plan_text(plan))
        keyboard = build_plan_keyboard(plan)
        last_index = len(chunks) - 1
        for index, chunk in enumerate(chunks):
            reply_markup = keyboard if index == last_index else None
            await self._bot.send_message(chat_id, chunk, reply_markup=reply_markup)

    async def send_article_ready(self, chat_id: int, article: ArticleView) -> None:
        document = BufferedInputFile(article.content, filename=article.filename)
        caption = f"📄 {article.title} ({article.platform})"
        await self._bot.send_document(chat_id, document, caption=caption)
        await self._bot.send_message(
            chat_id,
            "Принять эту Статью или запросить перегенерацию?",
            reply_markup=build_article_keyboard(article.id),
        )

    async def send_error(self, chat_id: int, text: str) -> None:
        for chunk in chunk_text(text):
            await self._bot.send_message(chat_id, chunk)


class TelegramCommentPrompt:
    """CommentPrompt implementation: asks for an optional comment with a Skip button."""

    def __init__(self, bot: BotClient) -> None:
        self._bot = bot

    async def prompt_for_comment(self, chat_id: int, id_: str) -> None:
        await self._bot.send_message(
            chat_id,
            "Комментарий к перегенерации? Одной строкой, или нажмите «Пропустить».",
            reply_markup=build_skip_keyboard(id_),
        )
