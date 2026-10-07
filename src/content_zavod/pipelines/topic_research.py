"""TopicResearcher: the once-per-Тема research -> outline half of the article pipeline (#94).

    search (Yandex Search API) -> candidate URLs (editorial policy) -> fetch pages
    -> per page, the model extracts facts STRICTLY from the page text, each with a quote
    -> code keeps only facts whose quote is really in the page (and whose numbers are in
       the quote) -> evidence bundle -> shared outline built from the evidence

The result (`TopicResearch`) is cached per Тема (`ResearchCache`, Postgres in production)
and reused by both Площадки and by every Перегенерация, so the Статьи of one Тема stand
on the same facts and research is paid for once.

Degraded modes never invent anything: no search provider, a failed search, or no fact
surviving verification all yield a bundle without evidence - the draft is then told to
write without concrete facts/numbers and the Статья carries a note for the editor. A
failed search (`search_unavailable`) is not cached, so the next Статья of the Тема tries
again; "searched but found nothing" (`no_evidence`) is.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from urllib.parse import urlsplit

from ..domain import (
    Evidence,
    PlanItemId,
    ResearchBundle,
    ResearchSource,
    TopicResearch,
    topic_fingerprint,
)
from ..yandex import Message, SearchResults
from .article_prompts import PAGE_TEXT_LIMIT, outline_messages, research_extract_messages
from .page_fetcher import FetchedPage, PageFetcher
from .provenance import StepRecord, prompt_hash

logger = logging.getLogger(__name__)

SEARCH_REQUEST_VERSION = "web-search-v1"

# Pages shorter than this (after HTML -> text) are menus, paywalls or stubs, not material.
MIN_PAGE_CHARS = 800
# A quote this short ("CRM", "42%") proves nothing about the fact it's attached to.
MIN_QUOTE_CHARS = 25
MAX_FACTS_PER_PAGE = 5
MAX_EVIDENCE = 15
# A source older than this many years is skipped when its date is known: stale numbers are
# worse than none (docs/plans/2026-08-12-technical-hardening-and-saas.md, E2).
MAX_SOURCE_AGE_YEARS = 3
# Time budget of the search call (Yandex web search takes 20-90s; the client retries
# connection errors) - past it the Тема is written in "no evidence" mode rather than the
# Job hanging. Pages are fetched in parallel, each under the fetcher's own total deadline,
# and facts are extracted from the pages in parallel, so research costs roughly one search
# + one page fetch + one extraction call of wall time, not their sum over the pages.
SEARCH_TIMEOUT_SECONDS = 120.0

# Video, social, marketplaces, search pages: not citable Источники for an article.
_BLOCKED_DOMAINS = (
    "youtube.com",
    "youtu.be",
    "rutube.ru",
    "vk.com",
    "vkvideo.ru",
    "ok.ru",
    "t.me",
    "instagram.com",
    "facebook.com",
    "tiktok.com",
    "pinterest.com",
    "pinterest.ru",
    "avito.ru",
    "ozon.ru",
    "wildberries.ru",
    "market.yandex.ru",
    "yandex.ru",
    "ya.ru",
)
_NON_HTML_EXTENSIONS = (
    ".pdf",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".ppt",
    ".pptx",
    ".zip",
    ".rar",
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".mp4",
    ".mp3",
)


class SearchProvider(Protocol):
    """Port in front of the web search (like `KeywordStats` for Wordstat): `yandex.WebSearch`
    in production, a fake in tests."""

    async def search(self, query: str, *, limit: int) -> SearchResults: ...


class ResearchCache(Protocol):
    async def get(self, plan_item_id: PlanItemId, fingerprint: str) -> TopicResearch | None: ...

    async def put(
        self, plan_item_id: PlanItemId, fingerprint: str, research: TopicResearch
    ) -> None: ...


class StepRunner(Protocol):
    """What the article pipeline lends the researcher: LLM calls that are recorded in the
    Job's provenance (#74), and a way to record the non-LLM search step."""

    async def llm(self, step_name: str, messages: list[Message]) -> str: ...

    def record(self, step: StepRecord) -> None: ...


@dataclass(frozen=True)
class TopicBrief:
    title: str
    summary: str
    keywords: Sequence[str]


class TopicResearcher:
    def __init__(
        self,
        search: SearchProvider | None,
        fetcher: PageFetcher,
        cache: ResearchCache | None = None,
        *,
        max_results: int = 8,
        max_pages: int = 4,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._search = search
        self._fetcher = fetcher
        self._cache = cache
        self._max_results = max_results
        self._max_pages = max_pages
        self._now = now

    async def prepare(
        self, brief: TopicBrief, *, plan_item_id: PlanItemId | None, steps: StepRunner
    ) -> TopicResearch:
        """The Тема's Исследование and shared outline - from the cache when this Тема (with
        this exact brief) was already researched, otherwise built and cached now."""
        fingerprint = topic_fingerprint(brief.title, brief.summary, brief.keywords)
        if self._cache is not None and plan_item_id is not None:
            cached = await self._cache.get(plan_item_id, fingerprint)
            if cached is not None:
                return cached
        bundle = await self._research(brief, steps)
        outline = await steps.llm(
            "outline",
            outline_messages(
                title=brief.title, summary=brief.summary, keywords=brief.keywords, bundle=bundle
            ),
        )
        research = TopicResearch(bundle=bundle, outline=outline)
        if (
            self._cache is not None
            and plan_item_id is not None
            and bundle.status != "search_unavailable"
        ):
            await self._cache.put(plan_item_id, fingerprint, research)
        return research

    async def _research(self, brief: TopicBrief, steps: StepRunner) -> ResearchBundle:
        query = search_query(brief)
        if self._search is None:
            return ResearchBundle(query=query, status="search_unavailable")
        try:
            results = await asyncio.wait_for(
                self._search.search(query, limit=self._max_results),
                timeout=SEARCH_TIMEOUT_SECONDS,
            )
        except Exception:
            logger.warning("research: web search failed for %r", query, exc_info=True)
            return ResearchBundle(query=query, status="search_unavailable")
        steps.record(
            StepRecord(
                step_name="research_search",
                provider="yandex",
                model="searchapi/web",
                params={"query": query, "limit": self._max_results},
                prompt_template_version=SEARCH_REQUEST_VERSION,
                prompt_hash=prompt_hash(query),
                tokens=None,
                usage_missing=False,
                latency_ms=results.latency_ms,
                cost=results.cost,
            )
        )
        pages = await self._fetch_pages([hit.url for hit in results.hits])
        titles = {hit.url: hit.title for hit in results.hits}
        extracted = await asyncio.gather(*(self._extract(brief, page, steps) for page in pages))
        evidence: list[Evidence] = []
        sources: list[ResearchSource] = []
        for page, facts in zip(pages, extracted, strict=True):
            if len(evidence) >= MAX_EVIDENCE:
                break
            if not facts:
                continue
            for fact, quote in facts[: MAX_EVIDENCE - len(evidence)]:
                evidence.append(
                    Evidence(id=f"E{len(evidence) + 1}", fact=fact, quote=quote, url=page.url)
                )
            sources.append(
                ResearchSource(
                    url=page.url,
                    title=page.title or titles.get(page.url, ""),
                    publisher=page.publisher,
                    published_at=page.published_at,
                    retrieved_at=self._now().isoformat(timespec="seconds"),
                    excerpt_hash=hashlib.sha256(page.text.encode("utf-8")).hexdigest()[:16],
                )
            )
        return ResearchBundle(
            query=query,
            status="ok" if evidence else "no_evidence",
            sources=tuple(sources),
            evidence=tuple(evidence),
        )

    async def _fetch_pages(self, urls: Sequence[str]) -> list[FetchedPage]:
        candidates = select_candidates(urls)
        fetched = await asyncio.gather(*(self._fetcher.fetch(url) for url in candidates))
        pages: list[FetchedPage] = []
        seen_domains: set[str] = set()
        for page in fetched:
            if page is None or len(page.text) < MIN_PAGE_CHARS:
                continue
            if self._is_stale(page.published_at):
                continue
            domain = _domain(page.url)
            if domain in seen_domains or not _passes_url_policy(page.url):
                continue
            seen_domains.add(domain)
            pages.append(page)
            if len(pages) >= self._max_pages:
                break
        return pages

    def _is_stale(self, published_at: str | None) -> bool:
        # ISO dates ("2022-03-01T...") and Russian ones ("01.03.2022") alike.
        match = re.search(r"(?<!\d)((?:19|20)\d{2})(?!\d)", published_at or "")
        if match is None:
            return False
        return int(match.group(1)) < self._now().year - MAX_SOURCE_AGE_YEARS

    async def _extract(
        self, brief: TopicBrief, page: FetchedPage, steps: StepRunner
    ) -> list[tuple[str, str]]:
        page_text = page.text[:PAGE_TEXT_LIMIT]
        try:
            answer = await steps.llm(
                "research_extract",
                research_extract_messages(
                    title=brief.title,
                    summary=brief.summary,
                    keywords=brief.keywords,
                    page_url=page.url,
                    page_title=page.title,
                    page_text=page_text,
                ),
            )
        except Exception:
            logger.warning("research: fact extraction failed for %s", page.url, exc_info=True)
            return []
        return verified_facts(answer, page_text)[:MAX_FACTS_PER_PAGE]


# Top Wordstat keywords of the Тема whose words are added to the search query.
MAX_QUERY_KEYWORDS = 2
_WORD_RE = re.compile(r"\w+")


def search_query(brief: TopicBrief) -> str:
    """The one search request of a Тема: its title plus the words of its top keywords that
    the title doesn't already contain.

    Titles are headlines ("5 ошибок при выборе CRM, которые стоят денег") - their catchy
    words dilute the search; the Wordstat keywords are what people actually search for on
    the subject, so they pull results towards real material. A word already in the title
    (compared by its first 5 letters, to absorb Russian inflection) is not repeated. The
    summary is left out: it is a whole sentence and would only blur the query."""
    seen = {word[:5].lower() for word in _WORD_RE.findall(brief.title)}
    extra: list[str] = []
    for keyword in brief.keywords[:MAX_QUERY_KEYWORDS]:
        for word in _WORD_RE.findall(keyword):
            stem = word[:5].lower()
            if stem not in seen:
                seen.add(stem)
                extra.append(word)
    return " ".join([" ".join(brief.title.split()), *extra])


def select_candidates(urls: Sequence[str]) -> list[str]:
    """Editorial URL policy before anything is fetched: no homepages, files, blocked
    domains, and one page per domain (in search rank order)."""
    selected: list[str] = []
    seen: set[str] = set()
    for url in urls:
        if not _passes_url_policy(url):
            continue
        domain = _domain(url)
        if domain in seen:
            continue
        seen.add(domain)
        selected.append(url)
    return selected


def _domain(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower()
    return host.removeprefix("www.")


def _passes_url_policy(url: str) -> bool:
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return False
    domain = _domain(url)
    if any(domain == blocked or domain.endswith(f".{blocked}") for blocked in _BLOCKED_DOMAINS):
        return False
    path = parts.path.lower()
    if path in ("", "/") and not parts.query:
        return False
    return not path.endswith(_NON_HTML_EXTENSIONS)


_NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)?")
_QUOTE_CHARS = str.maketrans(
    {"«": '"', "»": '"', "“": '"', "”": '"', "„": '"', "’": "'", "‘": "'", "`": "'"}
)
_DASHES = str.maketrans({"—": "-", "–": "-", "‑": "-", "‐": "-", "−": "-", "\xa0": " "})
# Soft hyphens and zero-width characters are common in Russian page markup (hyphenation,
# typographers) and invisible - a model never reproduces them in a quote.
_INVISIBLE = dict.fromkeys(map(ord, "\u00ad\u200b\u200c\u200d\u2060\ufeff"))


def _normalize(text: str) -> str:
    text = text.translate(_INVISIBLE).translate(_QUOTE_CHARS).translate(_DASHES)
    text = text.replace("…", "...").lower().replace("ё", "е")
    return " ".join(text.split())


def _numbers(text: str) -> set[str]:
    # "1 500" and "1500" are the same number (with a regular, no-break, thin or narrow
    # no-break space as the thousands separator); "3,5" and "3.5" too.
    joined = re.sub("(?<=\\d)[ \xa0\u2009\u202f](?=\\d{3}\\b)", "", text.translate(_INVISIBLE))
    return {match.replace(",", ".") for match in _NUMBER_RE.findall(joined)}


def verified_facts(answer: str, page_text: str) -> list[tuple[str, str]]:
    """Parses the model's JSON answer and keeps only facts it couldn't have made up: the
    quote must be a verbatim (whitespace/quote-style-insensitive) part of the page, and
    every number in the fact must appear in that quote."""
    try:
        start, end = answer.index("["), answer.rindex("]") + 1
        items = json.loads(answer[start:end])
    except ValueError:
        return []
    if not isinstance(items, list):
        return []
    page = _normalize(page_text)
    facts: list[tuple[str, str]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        fact = " ".join(str(item.get("fact", "")).split())
        quote = " ".join(str(item.get("quote", "")).split())
        if not fact or len(quote) < MIN_QUOTE_CHARS:
            continue
        if _normalize(quote) not in page:
            continue
        if not _numbers(fact) <= _numbers(quote):
            continue
        facts.append((fact, quote))
    return facts
