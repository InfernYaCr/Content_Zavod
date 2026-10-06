"""Article Markdown -> Telegraph Nodes (https://telegra.ph/api#Node).

Telegraph renders only a fixed tag set (no h1/h2, no tables), so headings collapse onto
`h3`/`h4` and anything unknown stays a plain paragraph. Like the .docx export
(`domain/export.py`), every non-empty line is its own block: the generation prompts ask the
model for Markdown, but it routinely uses a single newline as a paragraph break. Code
fences become one `pre` block.

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
_ORDERED_RE = re.compile(r"^\s*(\d+)[.)]\s+(.*)$")
_FENCE_RE = re.compile(r"^\s*(```|~~~)")
_QUOTE_RE = re.compile(r"^>\s?(.*)$")
_RULE_RE = re.compile(r"^\s*(?:-{3,}|\*{3,}|_{3,})\s*$")

# A URL may hold one level of balanced parentheses (`.../wiki/CRM_(software)`), so the `)`
# closing a Markdown link or a sentence's bracket is still left out.
_URL_CHAR = r"(?:[^\s()\]]|\([^\s()\]]*\))"

# One alternation, earliest match wins; the inner text of strong/em/link is parsed again
# so `**[ссылка](url)**` keeps both the bold and the link.
_INLINE_RE = re.compile(
    rf"(?P<link>\[(?P<link_text>[^\]]+)\]\((?P<link_url>https?://{_URL_CHAR}+)\))"
    r"|(?P<code>`(?P<code_text>[^`]+)`)"
    r"|(?P<strong>\*\*(?P<strong_text>.+?)\*\*|__(?P<strong_text2>.+?)__)"
    r"|(?P<em>(?<![\w*])\*(?P<em_text>[^\s*](?:.*?[^\s*])?)\*(?![\w*])"
    r"|(?<![\w_])_(?P<em_text2>[^\s_](?:.*?[^\s_])?)_(?![\w_]))"
    rf"|(?P<url>https?://{_URL_CHAR}*(?:[^\s()\].,;:!?»\"']|\([^\s()\]]*\)))"
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
    # The number the text gave each `ol` item: Telegraph has no `start` attribute, see
    # `_number_lists`.
    item_numbers: dict[int, int] = {}
    restarts_off_one = False
    code_lines: list[str] | None = None
    first_block = True
    for raw_line in markdown.splitlines():
        line = raw_line.rstrip()
        if code_lines is not None:
            if _FENCE_RE.match(line):
                nodes.append(_element("pre", ["\n".join(code_lines)]))
                code_lines = None
            else:
                code_lines.append(raw_line)
            continue
        if not line.strip():
            # A blank line between items doesn't end a list (a "loose" Markdown list).
            continue
        if _FENCE_RE.match(line):
            list_node = None
            code_lines = []
            first_block = False
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
            number = int(ordered.group(1))
            if (list_node is None or list_node["tag"] != "ol") and number != 1:
                restarts_off_one = True
            list_node = _append_list_item(nodes, list_node, "ol", ordered.group(2))
            item_numbers[id(list_node["children"][-1])] = number
        elif quote := _QUOTE_RE.match(line):
            list_node = None
            nodes.append(_element("blockquote", parse_inline(quote.group(1))))
        else:
            list_node = None
            nodes.append(_element("p", parse_inline(line.strip())))
        first_block = False
    if code_lines is not None:  # an unclosed fence still shows its code
        nodes.append(_element("pre", ["\n".join(code_lines)]))
    return _number_lists(nodes, item_numbers) if restarts_off_one else nodes


def _number_lists(nodes: list[Node], item_numbers: dict[int, int]) -> list[Node]:
    """Telegraph numbers every `ol` from 1, so a numbered list broken up by paragraphs
    (`1. Шаг` / текст / `2. Шаг`) would read «1. … 1. …». When any list continues a previous
    numbering, every numbered item in the Статья becomes a paragraph with its number written
    out as the text gave it - consistent and correct, at the cost of list indentation."""
    result: list[Node] = []
    for node in nodes:
        if isinstance(node, dict) and node.get("tag") == "ol":
            for item in node["children"]:
                number = item_numbers[id(item)]
                result.append(_element("p", [f"{number}. ", *item["children"]]))
        else:
            result.append(node)
    return result


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
