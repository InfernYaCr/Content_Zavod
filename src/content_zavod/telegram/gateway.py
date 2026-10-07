from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Protocol

from aiogram.types import (
    BotCommand,
    BotCommandScopeChat,
    BufferedInputFile,
    ForceReply,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)

from .article_card import render_article_card_text
from .callback_codec import (
    Action,
    ExportArticle,
    HistoryVersion,
    HistoryVersions,
    HistoryWeek,
    Page,
    SimpleAction,
    encode_callback_data,
)
from .pending_inputs import PendingInput
from .plan_hub import render_hub_screen
from .texts import (
    COVER_CAPTION,
    READ_BUTTON,
    REFINE_BUTTON,
    article_status,
    format_week_range,
    plan_status,
    platform_name,
    topic_status,
)
from .types import (
    ArticleFormat,
    ArticleSummary,
    ArticleVersionSummary,
    ArticleVersionView,
    ArticleView,
    PlanHubView,
    PlanSummary,
    PlanView,
    build_export_document,
    build_export_filename,
)

logger = logging.getLogger(__name__)

MESSAGE_LIMIT = 4096
ITEMS_PER_PAGE = 8


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


# A Тема's status as the Контент-менеджер reads it in the Plan message (#81) - the raw
# `plan_items.status` keys never reach the chat.
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
            status = topic_status(item.status)
            lines.append(f"{index}. {item.title} — {status}")
    return "\n".join(lines)


def build_plan_keyboard(plan: PlanView, *, page: int = 0) -> InlineKeyboardMarkup | None:
    """Edit buttons only for Темы still `pending_review`, labelled with the same absolute
    number `render_plan_text` shows (#81) - an approved, removed or archived Тема can't be
    edited anyway, so an approved Plan ends up with no edit buttons at all. "Утвердить всё"
    only while something is left to approve. `None` once no button is left, so the
    send/edit drops the keyboard instead of leaving a stale one."""
    page_count = total_pages(len(plan.items))
    start = page * ITEMS_PER_PAGE
    page_items = plan.items[start : start + ITEMS_PER_PAGE]
    rows: list[list[InlineKeyboardButton]] = []
    for number, item in enumerate(page_items, start=start + 1):
        if item.status != "pending_review":
            continue
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"🔄 {number}",
                    callback_data=encode_callback_data(SimpleAction("regenerate", item.id)),
                ),
                InlineKeyboardButton(
                    text=f"🗑 {number}",
                    callback_data=encode_callback_data(SimpleAction("delete", item.id)),
                ),
            ]
        )
    if page_count > 1:
        nav_row: list[InlineKeyboardButton] = []
        if page > 0:
            nav_row.append(
                InlineKeyboardButton(
                    text="◀ Назад",
                    callback_data=encode_callback_data(Page(plan.id, page - 1)),
                )
            )
        if page < page_count - 1:
            nav_row.append(
                InlineKeyboardButton(
                    text="Вперёд ▶",
                    callback_data=encode_callback_data(Page(plan.id, page + 1)),
                )
            )
        if nav_row:
            rows.append(nav_row)
    if any(item.status == "pending_review" for item in plan.items):
        rows.append(
            [
                InlineKeyboardButton(
                    text="✅ Утвердить всё",
                    callback_data=encode_callback_data(SimpleAction("approve_all", plan.id)),
                )
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None


def render_history_weeks_text(
    plans_page: Sequence[PlanSummary], *, page: int, page_count: int
) -> str:
    lines = ["🗂 История"]
    if page_count > 1:
        lines.append(f"Страница {page + 1}/{page_count}")
    lines.append("")
    if not plans_page:
        lines.append("Планов пока нет.")
        return "\n".join(lines)
    for item in plans_page:
        lines.append(f"{format_week_range(item.week_label)} — {plan_status(item.status)}")
    return "\n".join(lines)


def build_history_weeks_keyboard(
    plans_page: Sequence[PlanSummary], *, page: int, page_count: int
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(
                text=f"{format_week_range(item.week_label)} — {plan_status(item.status)}",
                callback_data=encode_callback_data(HistoryWeek(item.id, page)),
            )
        ]
        for item in plans_page
    ]
    if page_count > 1:
        nav_row: list[InlineKeyboardButton] = []
        if page > 0:
            nav_row.append(
                InlineKeyboardButton(
                    text="◀ Назад",
                    callback_data=encode_callback_data(SimpleAction("history_page", str(page - 1))),
                )
            )
        if page < page_count - 1:
            nav_row.append(
                InlineKeyboardButton(
                    text="Вперёд ▶",
                    callback_data=encode_callback_data(SimpleAction("history_page", str(page + 1))),
                )
            )
        if nav_row:
            rows.append(nav_row)
    return InlineKeyboardMarkup(inline_keyboard=rows)


# A Статья has a downloadable last Версия once it's left "queued"/"generating"/"error" -
# "regenerating" still serves its prior ready Версия, so it stays downloadable too (#30).
_DOWNLOADABLE_ARTICLE_STATUSES = frozenset({"ready", "regenerating", "exported"})

# A Статья has version history to browse as soon as it's recorded a first Версия - unlike
# export, that includes "error" (a later regeneration can fail after an earlier one
# succeeded, but the prior Версии are still there to look at). Only "queued"/"generating"
# are structurally guaranteed to have zero rows in article_versions (#26).
_VERSION_BROWSABLE_ARTICLE_STATUSES = frozenset({"ready", "regenerating", "exported", "error"})


def _export_button_row(
    article_id: str, *, docx_label: str, md_label: str
) -> list[InlineKeyboardButton]:
    """The .docx/.md export button pair, shared by the generation-time keyboard and the
    /history download row (#28/#30) - only the labels differ between the two call sites."""
    return [
        InlineKeyboardButton(
            text=docx_label,
            callback_data=encode_callback_data(ExportArticle(article_id, "docx")),
        ),
        InlineKeyboardButton(
            text=md_label,
            callback_data=encode_callback_data(ExportArticle(article_id, "md")),
        ),
    ]


def render_history_articles_text(
    plan_summary: PlanSummary, articles: Sequence[ArticleSummary]
) -> str:
    week = format_week_range(plan_summary.week_label)
    lines = [f"📄 Статьи: {week} ({plan_status(plan_summary.status)})", ""]
    if not articles:
        lines.append("Статей пока нет.")
        return "\n".join(lines)
    for index, item in enumerate(articles, start=1):
        platform = platform_name(item.platform)
        lines.append(f"{index}. {item.title} ({platform}) — {article_status(item.status)}")
    return "\n".join(lines)


def build_history_articles_keyboard(
    articles: Sequence[ArticleSummary], *, back_page: int
) -> InlineKeyboardMarkup:
    """One row per Статья that has recorded at least one Версия, numbered to match
    `render_history_articles_text` (#30). The row always carries a "Версии" button into the
    version history (#26); a downloadable last Версия additionally gets the .docx / .md export
    pair (#28). Articles still `queued`/`generating` have no Версия yet, so they get no row at
    all. Always ends with the "Назад" row."""
    rows: list[list[InlineKeyboardButton]] = []
    for index, item in enumerate(articles, start=1):
        if item.status not in _VERSION_BROWSABLE_ARTICLE_STATUSES:
            continue
        row: list[InlineKeyboardButton] = []
        if item.status in _DOWNLOADABLE_ARTICLE_STATUSES:
            row.extend(
                _export_button_row(
                    item.id, docx_label=f"⬇️ {index}. .docx", md_label=f"⬇️ {index}. .md"
                )
            )
        row.append(
            InlineKeyboardButton(
                text=f"🕓 {index}. Версии",
                callback_data=encode_callback_data(HistoryVersions(item.id, back_page)),
            )
        )
        rows.append(row)
    rows.append(
        [
            InlineKeyboardButton(
                text="◀ Назад",
                callback_data=encode_callback_data(SimpleAction("history_page", str(back_page))),
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _format_usage(tokens: int | None, cost: float | None) -> str:
    """Renders «н/д» rather than a bare 0 when a Версия's tokens/cost are unknown (#74) -
    e.g. a step didn't report usage or had no pricing configured."""
    tokens_text = f"{tokens} ток." if tokens is not None else "ток.: н/д"
    cost_text = f"{cost:.4f}" if cost is not None else "стоимость: н/д"
    return f"{tokens_text}, {cost_text}"


def render_history_versions_text(
    article: ArticleSummary, versions: Sequence[ArticleVersionSummary]
) -> str:
    lines = [f"🕓 Версии: {article.title} ({platform_name(article.platform)})", ""]
    if not versions:
        lines.append("Версий пока нет.")
        return "\n".join(lines)
    for index, version in enumerate(versions, start=1):
        lines.append(
            f"{index}. {version.created_at:%d.%m.%Y %H:%M} — {version.model}, "
            f"{_format_usage(version.tokens, version.cost)}"
        )
    return "\n".join(lines)


def build_history_versions_keyboard(
    article_id: str, plan_id: str, versions: Sequence[ArticleVersionSummary], *, back_page: int
) -> InlineKeyboardMarkup:
    """One button per Версия, numbered to match `render_history_versions_text`, opening that
    Версия's content (#26). "Назад" returns to this Статья's row in the article list, which is
    re-derived from `plan_id` rather than carried through the versions id (see
    `HistoryVersions`)."""
    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(
                text=f"{index}. {version.created_at:%d.%m %H:%M}",
                callback_data=encode_callback_data(
                    HistoryVersion(article_id, version.id, back_page)
                ),
            )
        ]
        for index, version in enumerate(versions, start=1)
    ]
    rows.append(
        [
            InlineKeyboardButton(
                text="◀ Назад",
                callback_data=encode_callback_data(HistoryWeek(plan_id, back_page)),
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


_TRUNCATION_NOTICE = "\n\n[…обрезано, версия длиннее лимита сообщения Telegram - только последняя версия доступна целиком через экспорт]"


def render_history_version_text(article: ArticleSummary, version: ArticleVersionView) -> str:
    header = (
        f"🕓 {article.title} ({platform_name(article.platform)})\n"
        f"{version.created_at:%d.%m.%Y %H:%M} — {version.model}, "
        f"{_format_usage(version.tokens, version.cost)}\n\n"
    )
    remaining = MESSAGE_LIMIT - len(header)
    content = version.content
    if len(content) > remaining:
        content = content[: remaining - len(_TRUNCATION_NOTICE)].rstrip() + _TRUNCATION_NOTICE
    return header + content


def build_history_version_keyboard(article_id: str, *, back_page: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="◀ Назад",
                    callback_data=encode_callback_data(HistoryVersions(article_id, back_page)),
                )
            ]
        ]
    )


def build_skip_keyboard(id_: str, action: Action = "regenerate") -> InlineKeyboardMarkup:
    """Пропустить re-sends `action` (regenerate without a comment); Отмена drops the wait (#88)."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Пропустить",
                    callback_data=encode_callback_data(SimpleAction(action, id_)),
                ),
                InlineKeyboardButton(
                    text="Отмена",
                    callback_data=encode_callback_data(SimpleAction("cancel_comment", id_)),
                ),
            ]
        ]
    )


def build_confirm_keyboard(id_: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Да",
                    callback_data=encode_callback_data(
                        SimpleAction("confirm_regenerate_plan", id_)
                    ),
                ),
                InlineKeyboardButton(
                    text="❌ Нет",
                    callback_data=encode_callback_data(SimpleAction("cancel_regenerate_plan", id_)),
                ),
            ]
        ]
    )


def build_persona_keyboard(templates: Sequence[tuple[str, str]]) -> InlineKeyboardMarkup:
    """One row per hardcoded Persona Preset, keyed by its position in `templates`
    rather than its (arbitrarily long) text, to stay within CALLBACK_DATA_LIMIT."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=title,
                    callback_data=encode_callback_data(
                        SimpleAction("persona_template", str(index))
                    ),
                )
            ]
            for index, (title, _text) in enumerate(templates)
        ]
    )


def build_retry_keyboard(job_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔁 Повторить",
                    callback_data=encode_callback_data(SimpleAction("retry", str(job_id))),
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
                    callback_data=encode_callback_data(
                        SimpleAction("request_access", str(telegram_id))
                    ),
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
                    callback_data=encode_callback_data(
                        SimpleAction("approve_join", str(join_request_id))
                    ),
                ),
                InlineKeyboardButton(
                    text="❌ Отклонить",
                    callback_data=encode_callback_data(
                        SimpleAction("decline_join", str(join_request_id))
                    ),
                ),
            ]
        ]
    )


def build_members_keyboard(
    members: list[tuple[int, str]], *, confirm_id: int | None = None
) -> InlineKeyboardMarkup:
    """One "Удалить" row per (telegram_id, label) member, for the /members command. The
    `confirm_id` member's row asks «Да, удалить / Отмена» instead (#90)."""
    rows: list[list[InlineKeyboardButton]] = []
    for telegram_id, label in members:
        if telegram_id == confirm_id:
            rows.append(
                [
                    InlineKeyboardButton(
                        text=f"✅ Да, удалить {label}",
                        callback_data=encode_callback_data(
                            SimpleAction("confirm_remove_member", str(telegram_id))
                        ),
                    ),
                    InlineKeyboardButton(
                        text="↩️ Отмена",
                        callback_data=encode_callback_data(
                            SimpleAction("cancel_remove_member", str(telegram_id))
                        ),
                    ),
                ]
            )
            continue
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"❌ Удалить {label}",
                    callback_data=encode_callback_data(
                        SimpleAction("remove_member", str(telegram_id))
                    ),
                )
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_article_keyboard(
    article_id: str,
    plan_item_id: str,
    *,
    read_url: str | None = None,
    exported: bool = False,
) -> InlineKeyboardMarkup:
    """«📖 Читать» opens the Статья's Страница для чтения (#92) and is left out when it
    couldn't be published; «✏️ Доработать» is the comment-gated Перегенерация. `exported`
    swaps ✅ for «✅ Готово» so the card shows it was accepted (#86); the button keeps the
    same `approve` callback, which is idempotent."""
    read = [InlineKeyboardButton(text=READ_BUTTON, url=read_url)] if read_url else []
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                *read,
                InlineKeyboardButton(
                    text=REFINE_BUTTON,
                    callback_data=encode_callback_data(
                        SimpleAction("regenerate_article", article_id)
                    ),
                ),
            ],
            _export_button_row(article_id, docx_label="⬇️ .docx", md_label="⬇️ .md"),
            [
                InlineKeyboardButton(
                    text="✅ Готово" if exported else "✅",
                    callback_data=encode_callback_data(SimpleAction("approve", article_id)),
                ),
                InlineKeyboardButton(
                    text="🖼 Обложка",
                    callback_data=encode_callback_data(SimpleAction("request_cover", plan_item_id)),
                ),
            ],
        ]
    )


class BotClient(Protocol):
    async def send_message(
        self,
        chat_id: int,
        text: str,
        reply_markup: InlineKeyboardMarkup | ForceReply | None = None,
        parse_mode: str | None = None,
    ) -> int:
        """Returns the sent message's id, so callers that need to address it later
        (e.g. editing a specific Owner's copy of a join-request broadcast) can.
        `ForceReply` and `parse_mode` exist for the comment prompt's mention (#88)."""
        ...

    async def send_document(
        self,
        chat_id: int,
        document: BufferedInputFile,
        caption: str | None = None,
    ) -> None: ...

    async def send_photo(
        self,
        chat_id: int,
        photo: BufferedInputFile,
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

    async def delete_message(self, chat_id: int, message_id: int) -> None:
        """Best effort: a message already gone (or too old to delete) is not an error."""
        ...

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

    async def send_plan(self, chat_id: int, plan: PlanView, *, page: int = 0) -> int:
        """Returns the sent message's id, so callers can record it as the Plan's canonical
        Telegram identity (see `telegram.plan_delivery.deliver_plan_message`, #73)."""
        return await self._bot.send_message(
            chat_id,
            render_plan_text(plan, page=page),
            reply_markup=build_plan_keyboard(plan, page=page),
        )

    async def edit_plan(
        self, chat_id: int, message_id: int, plan: PlanView, *, page: int = 0
    ) -> None:
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
            reply_markup=build_history_articles_keyboard(articles, back_page=back_page),
        )

    async def edit_history_versions(
        self,
        chat_id: int,
        message_id: int,
        article: ArticleSummary,
        plan_id: str,
        versions: Sequence[ArticleVersionSummary],
        *,
        back_page: int,
    ) -> None:
        await self._bot.edit_message_text(
            chat_id,
            message_id,
            render_history_versions_text(article, versions),
            reply_markup=build_history_versions_keyboard(
                article.id, plan_id, versions, back_page=back_page
            ),
        )

    async def edit_history_version(
        self,
        chat_id: int,
        message_id: int,
        article: ArticleSummary,
        version: ArticleVersionView,
        *,
        back_page: int,
    ) -> None:
        await self._bot.edit_message_text(
            chat_id,
            message_id,
            render_history_version_text(article, version),
            reply_markup=build_history_version_keyboard(article.id, back_page=back_page),
        )

    async def send_hub(self, chat_id: int, hub: PlanHubView) -> int:
        """The Хаб (#91) as a new message - only when the Plan has no canonical message yet;
        returns its id so the caller can record it (`telegram.plan_delivery`)."""
        text, keyboard = render_hub_screen(hub)
        return await self._bot.send_message(chat_id, text, reply_markup=keyboard)

    async def edit_hub(self, chat_id: int, message_id: int, hub: PlanHubView) -> None:
        """Redraws the Plan message as whatever Хаб screen is open (#91). An unchanged
        redraw (e.g. a redelivered notification) is a no-op success in the bot client."""
        text, keyboard = render_hub_screen(hub)
        await self._bot.edit_message_text(chat_id, message_id, text, reply_markup=keyboard)

    async def edit_notice(self, chat_id: int, message_id: int, text: str) -> None:
        await self._bot.edit_message_text(chat_id, message_id, text)

    async def send_article_ready(
        self, chat_id: int, article: ArticleView, *, read_url: str | None = None
    ) -> None:
        keyboard = build_article_keyboard(article.id, article.plan_item_id, read_url=read_url)
        await self._bot.send_message(
            chat_id, render_article_card_text(article), reply_markup=keyboard
        )

    async def mark_article_card_exported(
        self, chat_id: int, message_id: int, article: ArticleView, *, read_url: str | None = None
    ) -> None:
        """Redraws an Article card's buttons in place after ✅, text untouched (#86);
        `read_url` keeps «📖 Читать» on the redrawn card (#92)."""
        keyboard = build_article_keyboard(
            article.id, article.plan_item_id, read_url=read_url, exported=True
        )
        await self._bot.edit_message_reply_markup(chat_id, message_id, reply_markup=keyboard)

    async def send_article_document(
        self, chat_id: int, article: ArticleView, article_format: ArticleFormat
    ) -> None:
        filename = build_export_filename(article.title, article.platform, article_format)
        content = build_export_document(article, article_format)
        document = BufferedInputFile(content, filename=filename)
        caption = f"📄 {article.title} ({platform_name(article.platform)})"
        await self._bot.send_document(chat_id, document, caption=caption)

    async def send_cover(self, chat_id: int, image: bytes, mime_type: str, title: str) -> None:
        extension = mime_type.rpartition("/")[2] or "jpg"
        photo = BufferedInputFile(image, filename=f"cover.{extension}")
        await self._bot.send_photo(chat_id, photo, caption=COVER_CAPTION.format(title=title))

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

    async def send_notice(
        self, chat_id: int, text: str, reply_markup: InlineKeyboardMarkup | None = None
    ) -> None:
        """Plain informational text, for job results that aren't a rendered Plan/Article/error.
        `reply_markup`, if given, attaches only to the last chunk (e.g. /persona's Preset
        buttons - a message needing an on-topic keyboard is never long enough to actually split)."""
        chunks = chunk_text(text)
        last_index = len(chunks) - 1
        for index, chunk in enumerate(chunks):
            await self._bot.send_message(
                chat_id, chunk, reply_markup=reply_markup if index == last_index else None
            )


class TelegramCommentPrompt:
    """CommentPrompt implementation: asks for an optional comment with a Skip button.

    `action` is the callback action the Skip button re-sends - it must match
    whatever action originally opened the comment wait (`"regenerate"` for a
    Plan item, `"regenerate_article"` for an Article), otherwise Skip would
    route back into the wrong review flow (see #13 regenerate-misrouting fix).

    In a private chat the request is one message - the question with Пропустить/Отмена - and
    the user's next text is the comment. In a group it is two (#88): bots there (privacy mode)
    only see replies to their own messages, and a comment must not be any stray chat line, so a
    `ForceReply(selective=True)` line mentioning the asking user follows - their client opens a
    reply to it at once. Telegram allows one reply markup per message, hence the second message;
    it is transient and deleted as soon as the wait closes. On a regeneration the question is
    edited to "⏳ Генерирую..." (#80); on Отмена or a superseded wait both are deleted.
    """

    def __init__(self, bot: BotClient, *, action: Action = "regenerate") -> None:
        self._bot = bot
        self._action = action

    async def prompt_for_comment(
        self, chat_id: int, user_id: int, id_: str
    ) -> tuple[int, int | None]:
        # A private chat's id is its user's id; group and channel ids are negative.
        if chat_id == user_id:
            prompt_message_id = await self._bot.send_message(
                chat_id,
                "Комментарий к перегенерации? Напишите его следующим сообщением "
                "или нажмите «Пропустить».",
                reply_markup=build_skip_keyboard(id_, self._action),
            )
            return prompt_message_id, None
        prompt_message_id = await self._bot.send_message(
            chat_id,
            "Комментарий к перегенерации? Ответьте одной строкой, или нажмите «Пропустить».",
            reply_markup=build_skip_keyboard(id_, self._action),
        )
        force_reply_message_id = await self._bot.send_message(
            chat_id,
            f'✏️ <a href="tg://user?id={user_id}">Ваш комментарий</a> — ответом на это сообщение.',
            reply_markup=ForceReply(
                selective=True, input_field_placeholder="Комментарий к перегенерации"
            ),
            parse_mode="HTML",
        )
        return prompt_message_id, force_reply_message_id

    async def mark_generating(self, chat_id: int, pending: PendingInput) -> None:
        # Cosmetic: the regeneration is already enqueued, so a prompt the user deleted
        # meanwhile must not fail the update.
        try:
            await self._bot.edit_message_text(chat_id, pending.prompt_message_id, "⏳ Генерирую...")
        except Exception:
            logger.warning("could not mark comment prompt as generating", exc_info=True)
        if pending.force_reply_message_id is not None:
            await self._bot.delete_message(chat_id, pending.force_reply_message_id)

    async def withdraw(self, chat_id: int, pending: PendingInput) -> None:
        await self._bot.delete_message(chat_id, pending.prompt_message_id)
        if pending.force_reply_message_id is not None:
            await self._bot.delete_message(chat_id, pending.force_reply_message_id)
