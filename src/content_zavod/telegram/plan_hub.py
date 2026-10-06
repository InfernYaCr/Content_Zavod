"""plan_hub: rendering the Хаб - the approved Plan's canonical message (#91, ADR-0014).

After «Утвердить всё» the Plan message stops being a review list and becomes a checklist of
each Тема's cover and Статьи (⏳/✅/❌), redrawn from the DB on every generation notification.
A Тема whose cells are all finished gets a button that opens its result card in the same
message; «◀ К Плану» goes back. Which screen is open lives in `plans.hub_item_id`, so a
notification redraws the screen the team is actually looking at.

The Хаб is always a text message, never a photo: a text message can't be edited into a photo
message (or back), and a photo caption is capped at 1024 characters against a text message's
4096. So the cover and the Статьи are sent on demand as their own messages - the cover as a
photo, each Статья as its usual card with .docx/.md, ✏️ and ✅ (#92's «📖» page link sits
right in the result card) - instead of a burst of messages when generation ends.
"""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from ..domain import HubTopic, PlanHubView
from ..telegraph import page_url
from .callback_codec import SimpleAction, encode_callback_data
from .texts import (
    HUB_BUTTON_ARTICLE,
    HUB_BUTTON_BACK,
    HUB_BUTTON_COVER,
    HUB_BUTTON_READ,
    HUB_BUTTON_RETRY_ALL,
    HUB_BUTTON_RETRY_TOPIC,
    HUB_COVER_SHORT,
    HUB_DONE,
    HUB_DONE_WITH_FAILURES,
    HUB_EMPTY,
    HUB_PROGRESS,
    HUB_PROGRESS_HINT,
    HUB_TOPIC_ARTICLE_LINE,
    HUB_TOPIC_COVER_LINE,
    HUB_TOPIC_HEADER,
    HUB_TOPIC_HINT,
    format_week_range,
    hub_article_state,
    hub_cover_state,
    hub_mark,
    platform_name,
)

MESSAGE_LIMIT = 4096
# A Тема title is a short headline in practice; the caps only keep a pathological one from
# pushing the Хаб past Telegram's limits or wrapping a button over several lines on a phone.
_TITLE_LIMIT = 120
_BUTTON_TITLE_LIMIT = 40


def _shorten(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _topic_checklist_line(topic: HubTopic) -> str:
    cells = [f"{HUB_COVER_SHORT} {hub_mark(topic.cover)}"]
    cells += [f"{platform_name(c.platform)} {hub_mark(c.state)}" for c in topic.articles]
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
    if "pending" in cells and any(topic.finished for topic in hub.topics):
        lines += ["", HUB_PROGRESS_HINT]
    return _fit("\n".join(lines))


def build_hub_keyboard(hub: PlanHubView) -> InlineKeyboardMarkup | None:
    """One button per finished Тема (nothing ⏳ left in it), then «Повторить неудавшееся»
    while anything is ❌. `None` while nothing is clickable yet, so the edit drops the
    review keyboard instead of leaving a stale one."""
    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(
                text=f"📂 {topic.number}. {_shorten(topic.title, _BUTTON_TITLE_LIMIT)}",
                callback_data=encode_callback_data(SimpleAction("hub_topic", topic.id)),
            )
        ]
        for topic in hub.topics
        if topic.finished
    ]
    if "failed" in hub.cells:
        rows.append(
            [
                InlineKeyboardButton(
                    text=HUB_BUTTON_RETRY_ALL,
                    callback_data=encode_callback_data(SimpleAction("hub_retry", hub.id)),
                )
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None


def render_hub_topic_text(hub: PlanHubView, topic: HubTopic) -> str:
    lines = [
        HUB_TOPIC_HEADER.format(number=topic.number, total=len(hub.topics)),
        f"«{_shorten(topic.title, _TITLE_LIMIT)}»",
        "",
        HUB_TOPIC_COVER_LINE.format(mark=hub_mark(topic.cover), state=hub_cover_state(topic.cover)),
    ]
    for cell in topic.articles:
        lines.append(
            HUB_TOPIC_ARTICLE_LINE.format(
                platform=platform_name(cell.platform),
                mark=hub_mark(cell.state),
                state=hub_article_state(cell.state),
            )
        )
    if any(cell.has_content for cell in topic.articles):
        lines += ["", HUB_TOPIC_HINT]
    return _fit("\n".join(lines))


def build_hub_topic_keyboard(hub: PlanHubView, topic: HubTopic) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if topic.has_cover:
        rows.append(
            [
                InlineKeyboardButton(
                    text=HUB_BUTTON_COVER,
                    callback_data=encode_callback_data(SimpleAction("hub_cover", topic.id)),
                )
            ]
        )
    # One row per Площадка with a Версия: «📖» opens its Страница для чтения (#92) right in
    # Telegram, «📄» sends its usual card (downloads, ✏️ Доработать, ✅ Готово) below.
    for cell in topic.articles:
        if not cell.has_content or cell.article_id is None:
            continue
        platform = platform_name(cell.platform)
        row: list[InlineKeyboardButton] = []
        if cell.telegraph_path:
            row.append(
                InlineKeyboardButton(
                    text=HUB_BUTTON_READ.format(platform=platform),
                    url=page_url(cell.telegraph_path),
                )
            )
        row.append(
            InlineKeyboardButton(
                text=HUB_BUTTON_ARTICLE.format(platform=platform),
                callback_data=encode_callback_data(SimpleAction("hub_article", cell.article_id)),
            )
        )
        rows.append(row)
    if topic.has_failures:
        rows.append(
            [
                InlineKeyboardButton(
                    text=HUB_BUTTON_RETRY_TOPIC,
                    callback_data=encode_callback_data(SimpleAction("hub_retry_topic", topic.id)),
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text=HUB_BUTTON_BACK,
                callback_data=encode_callback_data(SimpleAction("hub_back", hub.id)),
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def render_hub_screen(hub: PlanHubView) -> tuple[str, InlineKeyboardMarkup | None]:
    """Whatever the Хаб message should show right now: the open Тема's result card, or the
    checklist when none is open (or the open one is no longer in the Хаб)."""
    topic = hub.open_topic
    if topic is not None:
        return render_hub_topic_text(hub, topic), build_hub_topic_keyboard(hub, topic)
    return render_hub_text(hub), build_hub_keyboard(hub)


def _fit(text: str) -> str:
    return text if len(text) <= MESSAGE_LIMIT else text[: MESSAGE_LIMIT - 1] + "…"
