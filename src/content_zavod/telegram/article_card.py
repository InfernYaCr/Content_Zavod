"""A Статья's text as the Хаб's Статья screen shows it (#92, #143): not the text itself, but
its opening 1–2 sentences with the Markdown stripped - the full text is one tap away, the
«📖 Читать полностью» Страница для чтения or the .docx/.md Выгрузка. Also the Protocol of the
Страница для чтения publisher the bot and the callback dispatcher share."""

from __future__ import annotations

import re
from typing import Protocol

from ..workspaces import WorkspaceId
from .types import ArticleView

PREVIEW_LIMIT = 800
OPENING_LIMIT = 220

_HEADING_RE = re.compile(r"^#{1,6}\s+(.*?)\s*#*\s*$")
_BULLET_RE = re.compile(r"^\s*[-*+]\s+")
_QUOTE_RE = re.compile(r"^>\s?")
_RULE_RE = re.compile(r"^\s*(?:-{3,}|\*{3,}|_{3,})\s*$")
_LINK_RE = re.compile(r"\[([^\]]+)\]\((?:[^)]+)\)")
_EMPHASIS_RE = re.compile(r"(\*\*|__|`)(.+?)\1|(?<![\w*])\*([^\s*](?:.*?[^\s*])?)\*(?![\w*])")
_SENTENCE_END_RE = re.compile(r"(?<=[.!?…])\s+")


def opening_text(markdown: str, *, title: str = "", limit: int = OPENING_LIMIT) -> str:
    """The Статья's first 1–2 sentences: its first plain paragraph (headings, lists, quotes and a
    line repeating the title skipped), the second sentence only if both fit in `limit`; a first
    sentence longer than that is cut on a word boundary with «…»."""
    paragraph = next(
        (
            _strip_inline(line.strip())
            for line in markdown.splitlines()
            if line.strip()
            and not _HEADING_RE.match(line.strip())
            and not _BULLET_RE.match(line)
            and not _RULE_RE.match(line)
            and not _QUOTE_RE.match(line.strip())
            and _normalize(line) != _normalize(title)
        ),
        None,
    )
    if paragraph is None:  # nothing but headings and lists: their text, flattened
        paragraph = plain_text_preview(markdown, title=title, limit=limit).replace("\n", " ")
    sentences = _SENTENCE_END_RE.split(paragraph)
    text = sentences[0]
    if len(sentences) > 1 and len(text) + 1 + len(sentences[1]) <= limit:
        text += " " + sentences[1]
    return plain_text_preview(text, limit=limit)


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
    """See `telegraph.TelegraphPublisher`: the Статья's page URL, `None` on failure. The
    Пространство names whose «Ваш канал или сайт» closes the page (ADR-0015)."""

    async def publish(self, workspace_id: WorkspaceId, article: ArticleView) -> str | None: ...
