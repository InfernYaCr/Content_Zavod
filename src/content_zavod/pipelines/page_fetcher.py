"""PageFetcher: downloads a search result's page and turns it into plain text for a Тема's
Исследование (#94).

Every fetched byte is untrusted: it only ever reaches a prompt as delimited INPUT_DATA
(see `article_prompts.research_extract_messages`). This module's job is to make sure we
fetch only what we should and get readable text out of it:

- URL policy against SSRF: http(s) only, default ports only, and every address the host
  resolves to must be a public (`is_global`) IP - checked again on every redirect hop,
  since redirects are followed by hand (at most `_MAX_REDIRECTS`).
- robots.txt is honoured per host (cached for the fetcher's lifetime); a robots.txt that
  can't be fetched counts as "allowed", 401/403 as "everything disallowed", as the
  stdlib parser does.
- Only `text/html` is read, streamed and capped at `max_bytes`; anything else is skipped.
- HTML -> text uses the stdlib `html.parser` (no new dependency): script/style/nav/
  footer-like blocks are dropped, `<article>`/`<main>` is preferred when it has enough
  text, and the title/publisher/published date are picked from the usual meta tags.

Never raises for a bad page: `fetch` returns `None` and the research step moves on.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import re
import socket
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Protocol
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import httpx

logger = logging.getLogger(__name__)

USER_AGENT = "ContentZavodBot/1.0 (research for article drafts)"
_ROBOTS_AGENT = "ContentZavodBot"
_MAX_REDIRECTS = 3
_ROBOTS_MAX_BYTES = 100_000

Resolver = Callable[[str, int], Awaitable[list[str]]]


@dataclass(frozen=True)
class FetchedPage:
    url: str
    title: str
    text: str
    publisher: str
    published_at: str | None


class PageFetcher(Protocol):
    """Port: a fake in tests, `HttpxPageFetcher` in production."""

    async def fetch(self, url: str) -> FetchedPage | None: ...


async def _system_resolver(host: str, port: int) -> list[str]:
    infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return [str(info[4][0]) for info in infos]


async def is_public_http_url(url: str, resolve: Resolver = _system_resolver) -> bool:
    """SSRF guard: only http(s) on the default port to a host whose every address is global."""
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return False
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return False
    if parts.username or parts.password:
        return False
    default_port = 443 if parts.scheme == "https" else 80
    if port not in (None, default_port):
        return False
    try:
        addresses = await resolve(parts.hostname, default_port)
    except OSError:
        return False
    if not addresses:
        return False
    for address in addresses:
        try:
            ip = ipaddress.ip_address(address.split("%", 1)[0])
        except ValueError:
            return False
        if not ip.is_global:
            return False
    return True


class HttpxPageFetcher:
    """`timeout` is httpx's per-operation timeout (connect, each read...); `total_timeout`
    caps a whole `fetch` - robots.txt, redirects and a server trickling bytes included - so
    one slow site can't stall a Тема's research."""

    def __init__(
        self,
        *,
        timeout: float = 15.0,
        total_timeout: float = 25.0,
        max_bytes: int = 2_000_000,
        transport: httpx.AsyncBaseTransport | None = None,
        resolve: Resolver = _system_resolver,
    ) -> None:
        self._client = httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=False,
            transport=transport,
            headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"},
        )
        self._max_bytes = max_bytes
        self._total_timeout = total_timeout
        self._resolve = resolve
        self._robots: dict[str, RobotFileParser] = {}

    async def fetch(self, url: str) -> FetchedPage | None:
        try:
            return await asyncio.wait_for(self._fetch(url), timeout=self._total_timeout)
        except TimeoutError:
            logger.info("research: page %s skipped (over %ss)", url, self._total_timeout)
            return None
        except (httpx.HTTPError, UnicodeError, ValueError) as exc:
            logger.info("research: page %s skipped (%s)", url, exc)
            return None

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _fetch(self, url: str) -> FetchedPage | None:
        current = url
        for _ in range(_MAX_REDIRECTS + 1):
            if not await is_public_http_url(current, self._resolve):
                logger.info("research: %s rejected by URL policy", current)
                return None
            if not await self._robots_allow(current):
                logger.info("research: %s disallowed by robots.txt", current)
                return None
            async with self._client.stream("GET", current) as response:
                if response.is_redirect:
                    location = response.headers.get("location")
                    if not location:
                        return None
                    current = urljoin(current, location)
                    continue
                if response.status_code != 200:
                    return None
                content_type = response.headers.get("content-type", "")
                if "html" not in content_type.lower():
                    return None
                raw = await _read_capped(response, self._max_bytes)
                html = raw.decode(_charset(response, raw), errors="replace")
            parsed = parse_html(html)
            return FetchedPage(
                url=current,
                title=parsed.title,
                text=parsed.text,
                publisher=parsed.publisher or (urlsplit(current).hostname or ""),
                published_at=parsed.published_at,
            )
        return None

    async def _robots_allow(self, url: str) -> bool:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        parser = self._robots.get(origin)
        if parser is None:
            parser = await self._load_robots(origin)
            self._robots[origin] = parser
        return parser.can_fetch(_ROBOTS_AGENT, url)

    async def _load_robots(self, origin: str) -> RobotFileParser:
        parser = RobotFileParser()
        try:
            async with self._client.stream("GET", f"{origin}/robots.txt") as response:
                if response.status_code in (401, 403):
                    parser.disallow_all = True
                    return parser
                if response.status_code != 200:
                    parser.allow_all = True
                    return parser
                raw = await _read_capped(response, _ROBOTS_MAX_BYTES)
        except httpx.HTTPError:
            parser.allow_all = True
            return parser
        parser.parse(raw.decode("utf-8", errors="replace").splitlines())
        return parser


async def _read_capped(response: httpx.Response, max_bytes: int) -> bytes:
    chunks: list[bytes] = []
    size = 0
    async for chunk in response.aiter_bytes():
        chunks.append(chunk)
        size += len(chunk)
        if size >= max_bytes:
            break
    return b"".join(chunks)[:max_bytes]


_META_CHARSET_RE = re.compile(rb"""<meta[^>]+charset=["']?([\w-]+)""", re.IGNORECASE)


def _charset(response: httpx.Response, raw: bytes) -> str:
    if response.charset_encoding:
        return response.charset_encoding
    match = _META_CHARSET_RE.search(raw[:4096])
    if match:
        name = match.group(1).decode("ascii", errors="ignore")
        try:
            "".encode(name)
        except LookupError:
            return "utf-8"
        return name
    return "utf-8"


@dataclass(frozen=True)
class ParsedHtml:
    title: str
    text: str
    publisher: str
    published_at: str | None


_SKIP_TAGS = frozenset(
    {
        "script",
        "style",
        "noscript",
        "svg",
        "nav",
        "footer",
        "header",
        "aside",
        "form",
        "iframe",
        "template",
        "button",
        "select",
    }
)
_VOID_TAGS = frozenset(
    {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "wbr"}
)
_BLOCK_TAGS = frozenset(
    {
        "p",
        "div",
        "section",
        "article",
        "main",
        "li",
        "ul",
        "ol",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "br",
        "tr",
        "table",
        "blockquote",
        "pre",
        "dd",
        "dt",
        "figcaption",
    }
)
_MAIN_TAGS = frozenset({"article", "main"})
_PUBLISHED_META = ("article:published_time", "datepublished", "date", "pubdate", "dc.date")
_MIN_LINE_WORDS = 3
# `<article>`/`<main>` is used instead of the whole page only when it holds real text.
_MIN_MAIN_CHARS = 500


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.skip_depth = 0
        self.main_depth = 0
        self.in_title = False
        self.title_parts: list[str] = []
        self.all_parts: list[str] = []
        self.main_parts: list[str] = []
        self.meta: dict[str, str] = {}
        self.first_time: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _VOID_TAGS:
            self.handle_startendtag(tag, attrs)
            return
        if tag in _SKIP_TAGS:
            self.skip_depth += 1
            return
        if tag == "title":
            self.in_title = True
        if tag == "time" and self.first_time is None:
            self.first_time = dict(attrs).get("datetime")
        if tag in _MAIN_TAGS:
            self.main_depth += 1
        if tag in _BLOCK_TAGS:
            self._newline()

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "meta":
            values = dict(attrs)
            key = values.get("property") or values.get("name") or values.get("itemprop") or ""
            content = values.get("content")
            if key and content:
                self.meta.setdefault(key.lower(), content.strip())
        elif tag == "br":
            self._newline()

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS:
            self.skip_depth = max(0, self.skip_depth - 1)
            return
        if tag == "title":
            self.in_title = False
        if tag in _MAIN_TAGS:
            self.main_depth = max(0, self.main_depth - 1)
        if tag in _BLOCK_TAGS:
            self._newline()

    def handle_data(self, data: str) -> None:
        if self.in_title:
            self.title_parts.append(data)
            return
        if self.skip_depth:
            return
        self.all_parts.append(data)
        if self.main_depth:
            self.main_parts.append(data)

    def _newline(self) -> None:
        self.all_parts.append("\n")
        if self.main_depth:
            self.main_parts.append("\n")


def _clean_text(parts: list[str]) -> str:
    lines = (" ".join(line.split()) for line in "".join(parts).splitlines())
    return "\n".join(line for line in lines if len(line.split()) >= _MIN_LINE_WORDS)


def parse_html(html: str) -> ParsedHtml:
    extractor = _TextExtractor()
    extractor.feed(html)
    extractor.close()
    main_text = _clean_text(extractor.main_parts)
    text = main_text if len(main_text) >= _MIN_MAIN_CHARS else _clean_text(extractor.all_parts)
    meta = extractor.meta
    title = meta.get("og:title") or " ".join("".join(extractor.title_parts).split())
    published_at = next((meta[key] for key in _PUBLISHED_META if meta.get(key)), None)
    return ParsedHtml(
        title=title,
        text=text,
        publisher=meta.get("og:site_name", ""),
        published_at=published_at or extractor.first_time,
    )
