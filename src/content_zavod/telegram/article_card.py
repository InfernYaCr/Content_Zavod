"""The ready-Статья card's text (#92): title, Площадка and a plain-text preview, so a
Контент-менеджер can judge the text on a phone without downloading a file. The full text is
one tap away - the «📖 Читать» Страница для чтения, or the .docx/.md Выгрузка. A Версия
written without verified evidence (#94) gets a warning line - kept out of the text itself."""

from __future__ import annotations

import re
from typing import Protocol

from .texts import ARTICLE_CARD_NO_EVIDENCE, ARTICLE_CARD_PLATFORM, platform_name
from .types import ArticleView

PREVIEW_LIMIT = 800

_HEADING_RE = re.compile(r"^#{1,6}\s+(.*?)\s*#*\s*$")
_BULLET_RE = re.compile(r"^\s*[-*+]\s+")
_QUOTE_RE = re.compile(r"^>\s?")
_RULE_RE = re.compile(r"^\s*(?:-{3,}|\*{3,}|_{3,})\s*$")
_LINK_RE = re.compile(r"\[([^\]]+)\]\((?:[^)]+)\)")
_EMPHASIS_RE = re.compile(r"(\*\*|__|`)(.+?)\1|(?<![\w*])\*([^\s*](?:.*?[^\s*])?)\*(?![\w*])")


def render_article_card_text(article: ArticleView) -> str:
    preview = plain_text_preview(article.content.decode("utf-8"), title=article.title)
    lines = [
        f"📄 {article.title}",
        ARTICLE_CARD_PLATFORM.format(platform=platform_name(article.platform)),
    ]
    warning = ARTICLE_CARD_NO_EVIDENCE.get(article.research_status or "")
    if warning:
        lines.append(warning)
    if preview:
        lines += ["", preview]
    return "\n".join(lines)


def plain_text_preview(markdown: str, *, title: str = "", limit: int = PREVIEW_LIMIT) -> str:
    """The Статья's opening ~`limit` characters with Markdown markup stripped, cut on a word
    boundary. A leading heading that just repeats the title is skipped."""
    lines: list[str] = []
    for raw_line in markdown.splitlines():
        line = raw_line.strip()
        if not line or _RULE_RE.match(line):
            continue
        if heading := _HEADING_RE.match(line):
            line = heading.group(1)
            if not lines and _normalize(line) == _normalize(title):
                continue
        elif _BULLET_RE.match(line):
            line = "• " + _BULLET_RE.sub("", line)
        else:
            line = _QUOTE_RE.sub("", line)
        lines.append(_strip_inline(line))
    text = "\n".join(lines)
    if len(text) <= limit:
        return text
    cut = text[:limit]
    boundary = max(cut.rfind(" "), cut.rfind("\n"))
    if boundary > limit // 2:
        cut = cut[:boundary]
    return cut.rstrip(" \n,.;:—-") + "…"


def _strip_inline(text: str) -> str:
    text = _LINK_RE.sub(r"\1", text)
    return _EMPHASIS_RE.sub(lambda m: m.group(2) or m.group(3), text)


def _normalize(value: str) -> str:
    return re.sub(r"[\W_]+", " ", value).strip().casefold()


class ArticlePagePublisher(Protocol):
    """See `telegraph.TelegraphPublisher`: the Статья's page URL, `None` on failure."""

    async def publish(self, article: ArticleView) -> str | None: ...


class ArticleCardSender(Protocol):
    async def send_article_ready(
        self, chat_id: int, article: ArticleView, *, read_url: str | None = None
    ) -> None: ...


async def send_article_card(
    gateway: ArticleCardSender,
    chat_id: int,
    view: ArticleView,
    publisher: ArticlePagePublisher | None,
) -> None:
    """Publishes (or updates) the Статья's Страница для чтения first, so the card can carry
    «📖 Читать» (#92). `publish` returns `None` on any Telegraph failure - the card still goes
    out, just without that button. Shared by a «✏️ Доработать» result and the Хаб's «📄»
    button (#91)."""
    read_url = await publisher.publish(view) if publisher is not None else None
    await gateway.send_article_ready(chat_id, view, read_url=read_url)
