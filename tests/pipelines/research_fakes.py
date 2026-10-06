"""Fakes for the research ports (search provider, page fetcher, research cache)."""

from __future__ import annotations

from content_zavod.domain import PlanItemId, TopicResearch
from content_zavod.pipelines.page_fetcher import FetchedPage
from content_zavod.yandex import SearchHit, SearchResults


class FakeSearch:
    def __init__(
        self,
        urls: list[str] | None = None,
        *,
        error: Exception | None = None,
        cost: float | None = 0.5,
    ) -> None:
        self._urls = urls or []
        self._error = error
        self._cost = cost
        self.queries: list[str] = []

    async def search(self, query: str, *, limit: int) -> SearchResults:
        self.queries.append(query)
        if self._error is not None:
            raise self._error
        hits = [SearchHit(url=url, title=f"title of {url}", snippet="") for url in self._urls]
        return SearchResults(hits=hits[:limit], latency_ms=3, cost=self._cost)


class FakePageFetcher:
    def __init__(self, pages: dict[str, FetchedPage] | None = None) -> None:
        self._pages = pages or {}
        self.fetched: list[str] = []

    async def fetch(self, url: str) -> FetchedPage | None:
        self.fetched.append(url)
        return self._pages.get(url)


class InMemoryResearchCache:
    def __init__(self) -> None:
        self.entries: dict[PlanItemId, tuple[str, TopicResearch]] = {}

    async def get(self, plan_item_id: PlanItemId, fingerprint: str) -> TopicResearch | None:
        entry = self.entries.get(plan_item_id)
        if entry is None or entry[0] != fingerprint:
            return None
        return entry[1]

    async def put(
        self, plan_item_id: PlanItemId, fingerprint: str, research: TopicResearch
    ) -> None:
        self.entries[plan_item_id] = (fingerprint, research)


def page(
    url: str, text: str, *, title: str = "Заголовок страницы", published_at: str | None = None
) -> FetchedPage:
    # Pad to a realistic page length so the tiny-page filter keeps it.
    filler = "\nДополнительный абзац текста страницы, не относящийся к фактам статьи." * 15
    return FetchedPage(
        url=url, title=title, text=text + filler, publisher="Издание", published_at=published_at
    )
