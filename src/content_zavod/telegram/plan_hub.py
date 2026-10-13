"""plan_hub: rendering the Хаб - the approved Plan's canonical message (#91, #143, ADR-0014).

After «Утвердить всё» the Plan message stops being a review list and becomes three screens in
that one message: the checklist of Темы (⏳/✅/❌/👍), a Тема's screen with its cover and
Статьи, and a Статья's screen - a summary with «📖 Читать полностью», «✅ Готово, публикую»,
«✏️ Попросить доработать» and the .docx/.md downloads. Every screen has its way back. Which
one is open lives in `plans.hub_item_id`/`hub_article_id`, so a notification redraws the screen
the reader is actually looking at.

The Хаб is always a text message, never a photo: a text message can't be edited into a photo
message (or back), and a photo caption is capped at 1024 characters against a text message's
4096. So the cover and the files are sent on demand as their own messages.
"""

from __future__ import annotations

import math

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from ..domain import HubArticleCell, HubTopic, PlanHubView
from ..telegraph import page_url
from .article_card import opening_text
from .callback_codec import ExportArticle, SimpleAction, encode_callback_data
from .texts import (
    ARTICLE_CARD_NO_EVIDENCE,
    HUB_ARTICLE_HEADER,
    HUB_ARTICLE_NEXT,
    HUB_ARTICLE_NEXT_NO_PAGE,
    HUB_ARTICLE_OPENING,
    HUB_ARTICLE_SIZE,
    HUB_ARTICLE_STATUS,
    HUB_BUTTON_APPROVE,
    HUB_BUTTON_APPROVED,
    HUB_BUTTON_ARTICLE,
    HUB_BUTTON_BACK,
    HUB_BUTTON_COVER,
    HUB_BUTTON_DOCX,
    HUB_BUTTON_MD,
    HUB_BUTTON_READ_FULL,
    HUB_BUTTON_REDRAW_COVER,
    HUB_BUTTON_REFINE,
    HUB_BUTTON_RETRY_ALL,
    HUB_BUTTON_RETRY_TOPIC,
    HUB_BUTTON_TO_TOPIC,
    HUB_COVER_SHORT,
    HUB_DONE,
    HUB_DONE_WITH_FAILURES,
    HUB_EMPTY,
    HUB_MARK_EXPORTED,
    HUB_NEXT,
    HUB_PROGRESS,
    HUB_TOPIC_ARTICLE_LINE,
    HUB_TOPIC_COVER_LINE,
    HUB_TOPIC_HEADER,
    HUB_TOPIC_NEXT,
    HUB_TOPIC_NEXT_DONE,
    HUB_TOPIC_NEXT_RETRY,
    HUB_TOPIC_NEXT_REWRITING,
    HUB_TOPIC_NEXT_WRITING,
    HUB_TOPIC_NO_EVIDENCE,
    format_week_range,
    hub_article_state,
    hub_cover_state,
    hub_mark,
    platform_genitive,
    platform_name,
)

MESSAGE_LIMIT = 4096
# A Тема title is a short headline in practice; the caps only keep a pathological one from
# pushing the Хаб past Telegram's limits or wrapping a button over several lines on a phone.
_TITLE_LIMIT = 120
_BUTTON_TITLE_LIMIT = 40
# Reading speed for «N мин чтения» (#143), characters per minute.
_CHARS_PER_MINUTE = 1300


def _shorten(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _cell_mark(cell: HubArticleCell) -> str:
    return HUB_MARK_EXPORTED if cell.state == "ready" and cell.exported else hub_mark(cell.state)


def _button(text: str, action: SimpleAction | ExportArticle) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=encode_callback_data(action))


def _topic_checklist_line(topic: HubTopic) -> str:
    cells = [] if topic.cover is None else [f"{HUB_COVER_SHORT} {hub_mark(topic.cover)}"]
    cells += [f"{platform_name(c.platform)} {_cell_mark(c)}" for c in topic.articles]
    return " · ".join(cells)


def render_hub_text(hub: PlanHubView) -> str:
    lines = [f"📋 План: {format_week_range(hub.week_label)}"]
    cells = hub.cells
    if not hub.topics:
        lines += ["", HUB_EMPTY]
        return "\n".join(lines)
    if "pending" in cells:
        done = sum(1 for cell in cells if cell != "pending")
        lines.append(HUB_PROGRESS.format(done=done, total=len(cells)))
    elif "failed" in cells:
        failed = sum(1 for cell in cells if cell == "failed")
        lines.append(HUB_DONE_WITH_FAILURES.format(failed=failed, total=len(cells)))
    else:
        lines.append(HUB_DONE)
    for topic in hub.topics:
        lines += ["", f"{topic.number}. {_shorten(topic.title, _TITLE_LIMIT)}"]
        lines.append(_topic_checklist_line(topic))
    if any(topic.openable for topic in hub.topics):
        lines += ["", HUB_NEXT]
    return _fit("\n".join(lines))


def build_hub_keyboard(hub: PlanHubView) -> InlineKeyboardMarkup | None:
    """One button per openable Тема (`HubTopic.openable`), then «Повторить неудавшееся»
    while anything is ❌. `None` while nothing is clickable yet, so the edit drops the
    review keyboard instead of leaving a stale one."""
    rows: list[list[InlineKeyboardButton]] = [
        [
            _button(
                f"📂 {topic.number}. {_shorten(topic.title, _BUTTON_TITLE_LIMIT)}",
                SimpleAction("hub_topic", topic.id),
            )
        ]
        for topic in hub.topics
        if topic.openable
    ]
    if "failed" in hub.cells:
        rows.append([_button(HUB_BUTTON_RETRY_ALL, SimpleAction("hub_retry", hub.id))])
    return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None


def _topic_next(topic: HubTopic) -> str:
    articles = topic.articles
    if articles and all(c.state == "ready" and c.exported for c in articles):
        return HUB_TOPIC_NEXT_DONE
    writing = any(c.state == "pending" for c in articles)
    if writing and all(c.state == "pending" for c in articles):
        if any(c.has_content for c in articles):
            return HUB_TOPIC_NEXT_REWRITING
        return HUB_TOPIC_NEXT_WRITING
    if not any(c.has_content for c in articles):  # nothing to open yet
        return HUB_TOPIC_NEXT_WRITING if writing else HUB_TOPIC_NEXT_RETRY
    return HUB_TOPIC_NEXT


def render_hub_topic_text(hub: PlanHubView, topic: HubTopic) -> str:
    lines = [
        HUB_TOPIC_HEADER.format(number=topic.number, total=len(hub.topics)),
        f"«{_shorten(topic.title, _TITLE_LIMIT)}»",
        "",
    ]
    for cell in topic.articles:
        state = hub_article_state(cell.state, has_content=cell.has_content, exported=cell.exported)
        lines.append(
            HUB_TOPIC_ARTICLE_LINE.format(
                platform=platform_name(cell.platform), mark=_cell_mark(cell), state=state
            )
        )
    if topic.cover is not None:
        lines.append(
            HUB_TOPIC_COVER_LINE.format(
                mark=hub_mark(topic.cover), state=hub_cover_state(topic.cover)
            )
        )
    # #94's ⚠️, so it isn't missed on the way to the Статья.
    for cell in topic.articles:
        warning = HUB_TOPIC_NO_EVIDENCE.get(cell.research_status or "")
        if warning and cell.has_content:
            lines.append(warning.format(platform=platform_name(cell.platform)))
    lines += ["", _topic_next(topic)]
    return _fit("\n".join(lines))


def build_hub_topic_keyboard(hub: PlanHubView, topic: HubTopic) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [
            _button(
                HUB_BUTTON_ARTICLE.format(platform=platform_genitive(cell.platform)),
                SimpleAction("hub_article", cell.article_id),
            )
        ]
        for cell in topic.articles
        if cell.has_content and cell.article_id is not None
    ]
    cover_row: list[InlineKeyboardButton] = []
    if topic.cover is not None and topic.has_cover:
        cover_row.append(_button(HUB_BUTTON_COVER, SimpleAction("hub_cover", topic.id)))
    if topic.cover == "ready":  # a new one by hand, sent as a photo (`request_cover`, #91)
        cover_row.append(_button(HUB_BUTTON_REDRAW_COVER, SimpleAction("request_cover", topic.id)))
    if cover_row:
        rows.append(cover_row)
    if topic.has_failures:
        rows.append([_button(HUB_BUTTON_RETRY_TOPIC, SimpleAction("hub_retry_topic", topic.id))])
    rows.append([_button(HUB_BUTTON_BACK, SimpleAction("hub_back", hub.id))])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _article_status(cell: HubArticleCell) -> str:
    return "exported" if cell.state == "ready" and cell.exported else cell.state


def _thousands(number: int) -> str:
    return f"{number:,}".replace(",", " ")  # «6 200», never wrapped between the groups


def render_hub_article_text(topic: HubTopic, cell: HubArticleCell) -> str:
    """The Статья screen (#143): what state it is in, how long it is, and its opening - a
    summary to decide what to do next, not a cut-off text."""
    status = _article_status(cell)
    # Both numbers from the one shown: half rounds up («6 250» → «6 300»).
    chars = max(100, int(cell.chars / 100 + 0.5) * 100)
    minutes = math.ceil(chars / _CHARS_PER_MINUTE)
    lines = [
        HUB_ARTICLE_HEADER.format(platform=platform_genitive(cell.platform)),
        f"«{_shorten(topic.title, _TITLE_LIMIT)}»",
        "",
        HUB_ARTICLE_STATUS[status],
        HUB_ARTICLE_SIZE.format(chars=_thousands(chars), minutes=minutes),
    ]
    warning = ARTICLE_CARD_NO_EVIDENCE.get(cell.research_status or "")
    if warning:
        lines.append(warning)
    opening = opening_text(cell.opening, title=topic.title)
    if opening:
        lines += ["", HUB_ARTICLE_OPENING.format(opening=opening)]
    next_line = HUB_ARTICLE_NEXT[status]
    if status == "ready" and not cell.telegraph_path:
        next_line = HUB_ARTICLE_NEXT_NO_PAGE
    lines += ["", next_line]
    return _fit("\n".join(lines))


def build_hub_article_keyboard(
    hub: PlanHubView, topic: HubTopic, cell: HubArticleCell
) -> InlineKeyboardMarkup:
    """«📖» only once the Страница для чтения is published; «✅ Готово, публикую» only on a
    Статья that is ready (👍 once marked - pressing it again is harmless); «✏️» not while a
    rewrite is already running."""
    assert cell.article_id is not None  # `PlanHubView.open_article` only yields one with a Версия
    article_id = cell.article_id
    rows: list[list[InlineKeyboardButton]] = []
    if cell.telegraph_path:
        rows.append(
            [InlineKeyboardButton(text=HUB_BUTTON_READ_FULL, url=page_url(cell.telegraph_path))]
        )
    if cell.state == "ready":
        label = HUB_BUTTON_APPROVED if cell.exported else HUB_BUTTON_APPROVE
        rows.append([_button(label, SimpleAction("hub_approve", article_id))])
    if cell.state != "pending":
        rows.append([_button(HUB_BUTTON_REFINE, SimpleAction("regenerate_article", article_id))])
    rows.append(
        [
            _button(HUB_BUTTON_DOCX, ExportArticle(article_id, "docx")),
            _button(HUB_BUTTON_MD, ExportArticle(article_id, "md")),
        ]
    )
    rows.append(
        [
            _button(HUB_BUTTON_TO_TOPIC, SimpleAction("hub_topic", topic.id)),
            _button(HUB_BUTTON_BACK, SimpleAction("hub_back", hub.id)),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def render_hub_screen(hub: PlanHubView) -> tuple[str, InlineKeyboardMarkup | None]:
    """Whatever the Хаб message should show right now: the open Статья, the open Тема, or the
    checklist when neither is open (or the open one is no longer in the Хаб)."""
    topic = hub.open_topic
    cell = hub.open_article
    if topic is not None and cell is not None:
        return render_hub_article_text(topic, cell), build_hub_article_keyboard(hub, topic, cell)
    if topic is not None:
        return render_hub_topic_text(hub, topic), build_hub_topic_keyboard(hub, topic)
    return render_hub_text(hub), build_hub_keyboard(hub)


def _fit(text: str) -> str:
    return text if len(text) <= MESSAGE_LIMIT else text[: MESSAGE_LIMIT - 1] + "…"
