from __future__ import annotations

from datetime import date
from typing import Literal, Protocol, Sequence

from aiogram.types import (
    BotCommand,
    BotCommandScopeChat,
    BufferedInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)

from .types import ArticleSummary, ArticleView, PlanSummary, PlanView

MESSAGE_LIMIT = 4096
CALLBACK_DATA_LIMIT = 64
ITEMS_PER_PAGE = 8

Action = Literal[
    "delete",
    "regenerate",
    "approve_all",
    "regenerate_article",
    "approve",
    "page",
    "confirm_regenerate_plan",
    "cancel_regenerate_plan",
    "retry",
    "request_access",
    "approve_join",
    "decline_join",
    "remove_member",
    "history_page",
    "history_week",
]

# Codes are 1-2 chars to leave room for id payloads within CALLBACK_DATA_LIMIT.
# "page" packs "<plan_id>:<page>" into its id via encode_page_callback/decode_page_id -
# for a 32-char uuid4().hex plan id that's "pg:" + 32 + ":" + up to 3 digits, ~38 bytes.
_ACTION_CODES: dict[Action, str] = {
    "delete": "d",
    "regenerate": "r",
    "approve_all": "a",
    "regenerate_article": "ar",
    "approve": "p",
    "page": "pg",
    "confirm_regenerate_plan": "cy",
    "cancel_regenerate_plan": "cn",
    "retry": "rt",
    "request_access": "ra",
    "approve_join": "aj",
    "decline_join": "dj",
    "remove_member": "rm",
    "history_page": "hp",
    "history_week": "hw",
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


def encode_page_callback(plan_id: str, page: int) -> str:
    return encode_callback_data("page", f"{plan_id}:{page}")


def decode_page_id(id_: str) -> tuple[str, int]:
    plan_id, _, page = id_.rpartition(":")
    return plan_id, int(page)


def encode_history_page_callback(page: int) -> str:
    return encode_callback_data("history_page", str(page))


def encode_history_week_callback(plan_id: str, page: int) -> str:
    """`page` is the week-list page the button was shown on, so the article screen's
    "Назад" button can return to that exact page instead of always page 0."""
    return encode_callback_data("history_week", f"{plan_id}:{page}")


def decode_history_week_id(id_: str) -> tuple[str, int]:
    plan_id, _, page = id_.rpartition(":")
    return plan_id, int(page)


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


def total_pages(item_count: int) -> int:
    return max(1, -(-item_count // ITEMS_PER_PAGE))  # ceil division


_MONTHS_RU_GENITIVE = (
    "января",
    "февраля",
    "марта",
    "апреля",
    "мая",
    "июня",
    "июля",
    "августа",
    "сентября",
    "октября",
    "ноября",
    "декабря",
)


def format_week_range(week_label: str) -> str:
    """Render an ISO week_label (e.g. "2026-W33") as a human date range, e.g.
    "10–16 августа 2026". `week_label` itself stays the Plan's idempotency
    key (see `week_label_for` in scheduling/weekly_plan_trigger.py) and is
    never shown to the Контент-менеджер directly."""
    year_part, _, week_part = week_label.partition("-W")
    monday = date.fromisocalendar(int(year_part), int(week_part), 1)
    sunday = date.fromisocalendar(int(year_part), int(week_part), 7)
    start_month = _MONTHS_RU_GENITIVE[monday.month - 1]
    end_month = _MONTHS_RU_GENITIVE[sunday.month - 1]
    if monday.year != sunday.year:
        return f"{monday.day} {start_month} {monday.year} – {sunday.day} {end_month} {sunday.year}"
    if monday.month != sunday.month:
        return f"{monday.day} {start_month} – {sunday.day} {end_month} {sunday.year}"
    return f"{monday.day}–{sunday.day} {end_month} {sunday.year}"


def render_plan_text(plan: PlanView, *, page: int = 0) -> str:
    page_count = total_pages(len(plan.items))
    start = page * ITEMS_PER_PAGE
    lines = [f"📋 План: {format_week_range(plan.week_label)}"]
    if page_count > 1:
        lines.append(f"Страница {page + 1}/{page_count}")
    lines.append("")
    # Numbered by absolute position across pages, not reset per page, so an
    # item number always refers to the same item regardless of which page shows it.
    for index, item in enumerate(plan.items, start=1):
        if start < index <= start + ITEMS_PER_PAGE:
            lines.append(f"{index}. {item.title} — {item.status}")
    return "\n".join(lines)


def build_plan_keyboard(plan: PlanView, *, page: int = 0) -> InlineKeyboardMarkup:
    page_count = total_pages(len(plan.items))
    start = page * ITEMS_PER_PAGE
    page_items = plan.items[start : start + ITEMS_PER_PAGE]
    rows: list[list[InlineKeyboardButton]] = []
    for item in page_items:
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
    if page_count > 1:
        nav_row: list[InlineKeyboardButton] = []
        if page > 0:
            nav_row.append(
                InlineKeyboardButton(
                    text="◀ Назад", callback_data=encode_page_callback(plan.id, page - 1)
                )
            )
        if page < page_count - 1:
            nav_row.append(
                InlineKeyboardButton(
                    text="Вперёд ▶", callback_data=encode_page_callback(plan.id, page + 1)
                )
            )
        if nav_row:
            rows.append(nav_row)
    rows.append(
        [
            InlineKeyboardButton(
                text="✅ Утвердить всё",
                callback_data=encode_callback_data("approve_all", plan.id),
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def render_history_weeks_text(plans_page: Sequence[PlanSummary], *, page: int, page_count: int) -> str:
    lines = ["🗂 История"]
    if page_count > 1:
        lines.append(f"Страница {page + 1}/{page_count}")
    lines.append("")
    if not plans_page:
        lines.append("Планов пока нет.")
        return "\n".join(lines)
    for item in plans_page:
        lines.append(f"{format_week_range(item.week_label)} — {item.status}")
    return "\n".join(lines)


def build_history_weeks_keyboard(
    plans_page: Sequence[PlanSummary], *, page: int, page_count: int
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(
                text=f"{format_week_range(item.week_label)} — {item.status}",
                callback_data=encode_history_week_callback(item.id, page),
            )
        ]
        for item in plans_page
    ]
    if page_count > 1:
        nav_row: list[InlineKeyboardButton] = []
        if page > 0:
            nav_row.append(
                InlineKeyboardButton(text="◀ Назад", callback_data=encode_history_page_callback(page - 1))
            )
        if page < page_count - 1:
            nav_row.append(
                InlineKeyboardButton(text="Вперёд ▶", callback_data=encode_history_page_callback(page + 1))
            )
        if nav_row:
            rows.append(nav_row)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def render_history_articles_text(plan_summary: PlanSummary, articles: Sequence[ArticleSummary]) -> str:
    lines = [f"📄 Статьи: {format_week_range(plan_summary.week_label)} ({plan_summary.status})", ""]
    if not articles:
        lines.append("Статей пока нет.")
        return "\n".join(lines)
    for item in articles:
        lines.append(f"{item.title} ({item.platform}) — {item.status}")
    return "\n".join(lines)


def build_history_articles_keyboard(*, back_page: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="◀ Назад", callback_data=encode_history_page_callback(back_page))]
        ]
    )


def build_skip_keyboard(id_: str, action: Action = "regenerate") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Пропустить",
                    callback_data=encode_callback_data(action, id_),
                )
            ]
        ]
    )


def build_confirm_keyboard(id_: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Да", callback_data=encode_callback_data("confirm_regenerate_plan", id_)
                ),
                InlineKeyboardButton(
                    text="❌ Нет", callback_data=encode_callback_data("cancel_regenerate_plan", id_)
                ),
            ]
        ]
    )


def build_retry_keyboard(job_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔁 Повторить", callback_data=encode_callback_data("retry", str(job_id))
                )
            ]
        ]
    )


def build_request_access_keyboard(telegram_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Запросить доступ",
                    callback_data=encode_callback_data("request_access", str(telegram_id)),
                )
            ]
        ]
    )


def build_join_request_keyboard(join_request_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Одобрить",
                    callback_data=encode_callback_data("approve_join", str(join_request_id)),
                ),
                InlineKeyboardButton(
                    text="❌ Отклонить",
                    callback_data=encode_callback_data("decline_join", str(join_request_id)),
                ),
            ]
        ]
    )


def build_members_keyboard(members: list[tuple[int, str]]) -> InlineKeyboardMarkup:
    """One "Удалить" row per (telegram_id, role) member, for the /members command."""
    rows = [
        [
            InlineKeyboardButton(
                text=f"❌ Удалить {telegram_id} ({role})",
                callback_data=encode_callback_data("remove_member", str(telegram_id)),
            )
        ]
        for telegram_id, role in members
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_article_keyboard(article_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔄 Перегенерировать",
                    callback_data=encode_callback_data("regenerate_article", article_id),
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
    ) -> int:
        """Returns the sent message's id, so callers that need to address it later
        (e.g. editing a specific Owner's copy of a join-request broadcast) can."""
        ...

    async def send_document(
        self,
        chat_id: int,
        document: BufferedInputFile,
        caption: str | None = None,
    ) -> None: ...

    async def edit_message_text(
        self,
        chat_id: int,
        message_id: int,
        text: str,
        reply_markup: InlineKeyboardMarkup | None = None,
    ) -> None: ...

    async def edit_message_reply_markup(
        self,
        chat_id: int,
        message_id: int,
        reply_markup: InlineKeyboardMarkup | None = None,
    ) -> None: ...

    async def set_my_commands(
        self, commands: list[BotCommand], *, scope: BotCommandScopeChat
    ) -> None: ...


class TelegramGateway:
    """Thin adapter over a Telegram bot client: rendering, chunking, keyboards only."""

    def __init__(self, bot: BotClient) -> None:
        self._bot = bot

    async def send_message(
        self, chat_id: int, text: str, reply_markup: InlineKeyboardMarkup | None = None
    ) -> int:
        """Generic send returning the message id - for flows that need to address
        this exact message later (e.g. editing a join-request broadcast)."""
        return await self._bot.send_message(chat_id, text, reply_markup=reply_markup)

    async def send_plan(self, chat_id: int, plan: PlanView, *, page: int = 0) -> None:
        await self._bot.send_message(
            chat_id, render_plan_text(plan, page=page), reply_markup=build_plan_keyboard(plan, page=page)
        )

    async def edit_plan(self, chat_id: int, message_id: int, plan: PlanView, *, page: int = 0) -> None:
        await self._bot.edit_message_text(
            chat_id,
            message_id,
            render_plan_text(plan, page=page),
            reply_markup=build_plan_keyboard(plan, page=page),
        )

    async def send_history_weeks(
        self, chat_id: int, plans_page: Sequence[PlanSummary], *, page: int, page_count: int
    ) -> int:
        return await self._bot.send_message(
            chat_id,
            render_history_weeks_text(plans_page, page=page, page_count=page_count),
            reply_markup=build_history_weeks_keyboard(plans_page, page=page, page_count=page_count),
        )

    async def edit_history_weeks(
        self,
        chat_id: int,
        message_id: int,
        plans_page: Sequence[PlanSummary],
        *,
        page: int,
        page_count: int,
    ) -> None:
        await self._bot.edit_message_text(
            chat_id,
            message_id,
            render_history_weeks_text(plans_page, page=page, page_count=page_count),
            reply_markup=build_history_weeks_keyboard(plans_page, page=page, page_count=page_count),
        )

    async def edit_history_articles(
        self,
        chat_id: int,
        message_id: int,
        plan_summary: PlanSummary,
        articles: Sequence[ArticleSummary],
        *,
        back_page: int,
    ) -> None:
        await self._bot.edit_message_text(
            chat_id,
            message_id,
            render_history_articles_text(plan_summary, articles),
            reply_markup=build_history_articles_keyboard(back_page=back_page),
        )

    async def edit_notice(self, chat_id: int, message_id: int, text: str) -> None:
        await self._bot.edit_message_text(chat_id, message_id, text)

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

    async def send_error_with_retry(self, chat_id: int, text: str, job_id: int) -> None:
        """Like send_error, but attaches a "Повторить" button for a background job that failed."""
        chunks = chunk_text(text)
        last_index = len(chunks) - 1
        for index, chunk in enumerate(chunks):
            reply_markup = build_retry_keyboard(job_id) if index == last_index else None
            await self._bot.send_message(chat_id, chunk, reply_markup=reply_markup)

    async def send_notice(self, chat_id: int, text: str) -> None:
        """Plain informational text, for job results that aren't a rendered Plan/Article/error."""
        for chunk in chunk_text(text):
            await self._bot.send_message(chat_id, chunk)


class TelegramCommentPrompt:
    """CommentPrompt implementation: asks for an optional comment with a Skip button.

    `action` is the callback action the Skip button re-sends - it must match
    whatever action originally opened the comment wait (`"regenerate"` for a
    Plan item, `"regenerate_article"` for an Article), otherwise Skip would
    route back into the wrong review flow (see #13 regenerate-misrouting fix).
    """

    def __init__(self, bot: BotClient, *, action: Action = "regenerate") -> None:
        self._bot = bot
        self._action = action

    async def prompt_for_comment(self, chat_id: int, id_: str) -> None:
        await self._bot.send_message(
            chat_id,
            "Комментарий к перегенерации? Одной строкой, или нажмите «Пропустить».",
            reply_markup=build_skip_keyboard(id_, self._action),
        )
