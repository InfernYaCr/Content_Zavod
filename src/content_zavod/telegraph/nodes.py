"""Article Markdown -> Telegraph Nodes (https://telegra.ph/api#Node).

Telegraph renders only a fixed tag set (no h1/h2, no tables), so headings collapse onto
`h3`/`h4` and anything unknown stays a plain paragraph. Like the .docx export
(`domain/export.py`), every non-empty line is its own block: the generation prompts ask the
model for Markdown, but it routinely uses a single newline as a paragraph break.

`fit_content` keeps a page under Telegraph's ~64 KB content limit by dropping trailing
blocks and pointing the reader at the downloadable file instead of failing the publish.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

Node = str | dict[str, Any]

# Telegraph's documented limit is 64 KB of serialized content; keep headroom for the
# footer, the truncation notice and form encoding.
CONTENT_LIMIT_BYTES = 60_000

_TRUNCATED_NOTICE = "Текст сокращён для этой страницы — полная версия в файле .docx/.md."

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_BULLET_RE = re.compile(r"^\s*[-*+•]\s+(.*)$")
_ORDERED_RE = re.compile(r"^\s*\d+[.)]\s+(.*)$")
_QUOTE_RE = re.compile(r"^>\s?(.*)$")
_RULE_RE = re.compile(r"^\s*(?:-{3,}|\*{3,}|_{3,})\s*$")

# One alternation, earliest match wins; the inner text of strong/em/link is parsed again
# so `**[ссылка](url)**` keeps both the bold and the link.
_INLINE_RE = re.compile(
    r"(?P<link>\[(?P<link_text>[^\]]+)\]\((?P<link_url>https?://[^\s)]+)\))"
    r"|(?P<code>`(?P<code_text>[^`]+)`)"
    r"|(?P<strong>\*\*(?P<strong_text>.+?)\*\*|__(?P<strong_text2>.+?)__)"
    r"|(?P<em>(?<![\w*])\*(?P<em_text>[^\s*](?:.*?[^\s*])?)\*(?![\w*])"
    r"|(?<![\w_])_(?P<em_text2>[^\s_](?:.*?[^\s_])?)_(?![\w_]))"
    r"|(?P<url>https?://[^\s)\]]+[^\s)\].,;:!?»\"'])"
)


@dataclass(frozen=True)
class FooterLink:
    """An optional closing link on the page (e.g. the customer's project, #98)."""

    text: str
    url: str


def markdown_to_nodes(markdown: str, *, title: str | None = None) -> list[Node]:
    """Converts an Article's Markdown into Telegraph block nodes. A leading `# <title>` line
    that repeats the page `title` is dropped - Telegraph already renders the title."""
    nodes: list[Node] = []
    list_node: dict[str, Any] | None = None
    first_block = True
    for raw_line in markdown.splitlines():
        line = raw_line.rstrip()
        if not line.strip():
            list_node = None
            continue
        heading = _HEADING_RE.match(line)
        if heading:
            list_node = None
            text = heading.group(2)
            if first_block and title is not None and _same_text(text, title):
                first_block = False
                continue
            tag = "h3" if len(heading.group(1)) <= 2 else "h4"
            nodes.append(_element(tag, parse_inline(text)))
        elif _RULE_RE.match(line):
            list_node = None
            nodes.append({"tag": "hr"})
        elif bullet := _BULLET_RE.match(line):
            list_node = _append_list_item(nodes, list_node, "ul", bullet.group(1))
        elif ordered := _ORDERED_RE.match(line):
            list_node = _append_list_item(nodes, list_node, "ol", ordered.group(1))
        elif quote := _QUOTE_RE.match(line):
            list_node = None
            nodes.append(_element("blockquote", parse_inline(quote.group(1))))
        else:
            list_node = None
            nodes.append(_element("p", parse_inline(line.strip())))
        first_block = False
    return nodes


def parse_inline(text: str) -> list[Node]:
    children: list[Node] = []
    position = 0
    for match in _INLINE_RE.finditer(text):
        if match.start() > position:
            children.append(text[position : match.start()])
        children.append(_inline_node(match))
        position = match.end()
    if position < len(text):
        children.append(text[position:])
    return children


def footer_nodes(footer: FooterLink) -> list[Node]:
    return [{"tag": "hr"}, _element("p", [_link(footer.url, [footer.text])])]


def content_size(nodes: list[Node]) -> int:
    return len(json.dumps(nodes, ensure_ascii=False).encode("utf-8"))


def fit_content(
    nodes: list[Node], *, tail: list[Node] | None = None, limit: int = CONTENT_LIMIT_BYTES
) -> list[Node]:
    """Returns `nodes + tail` if that fits `limit`, otherwise as many leading `nodes` as fit,
    followed by a truncation notice and `tail` (the footer is never the part that's cut)."""
    tail = tail or []
    if content_size(nodes + tail) <= limit:
        return nodes + tail
    notice: list[Node] = [_element("p", [_element("em", [_TRUNCATED_NOTICE])])]
    kept = list(nodes)
    while kept and content_size(kept + notice + tail) > limit:
        kept.pop()
    return kept + notice + tail


def _inline_node(match: re.Match[str]) -> Node:
    if match.group("link"):
        return _link(match.group("link_url"), parse_inline(match.group("link_text")))
    if match.group("code"):
        return _element("code", [match.group("code_text")])
    if match.group("strong"):
        inner = match.group("strong_text") or match.group("strong_text2")
        return _element("strong", parse_inline(inner))
    if match.group("em"):
        inner = match.group("em_text") or match.group("em_text2")
        return _element("em", parse_inline(inner))
    url = match.group("url")
    return _link(url, [url])


def _append_list_item(
    nodes: list[Node], list_node: dict[str, Any] | None, tag: str, text: str
) -> dict[str, Any]:
    if list_node is None or list_node["tag"] != tag:
        list_node = {"tag": tag, "children": []}
        nodes.append(list_node)
    list_node["children"].append(_element("li", parse_inline(text)))
    return list_node


def _element(tag: str, children: list[Node]) -> dict[str, Any]:
    return {"tag": tag, "children": children}


def _link(url: str, children: list[Node]) -> dict[str, Any]:
    return {"tag": "a", "attrs": {"href": url}, "children": children}


def _same_text(left: str, right: str) -> bool:
    def normalize(value: str) -> str:
        return re.sub(r"[\W_]+", " ", value).strip().casefold()

    return normalize(left) == normalize(right)
