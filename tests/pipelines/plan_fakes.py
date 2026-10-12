"""Fakes for the Карта Ниши and the weekly Plan (#128): Wordstat, the model, news search and the
Пространство's key-value store - no network."""

from __future__ import annotations

from datetime import date, timedelta

from content_zavod.yandex import (
    Completion,
    KeywordDynamicsPoint,
    KeywordStat,
    Message,
    RateLimited,
    SearchHit,
    SearchResults,
    TopRequests,
    YandexError,
)


def top(
    frequency: int, *, results: dict[str, int] | None = None, assoc: dict[str, int] | None = None
) -> TopRequests:
    return TopRequests(
        frequency=frequency,
        results=[KeywordStat(k, v) for k, v in (results or {}).items()],
        associations=[KeywordStat(k, v) for k, v in (assoc or {}).items()],
    )


def weekly(counts: list[int]) -> list[KeywordDynamicsPoint]:
    start = date(2025, 8, 18)
    return [
        KeywordDynamicsPoint(date=f"{start + timedelta(weeks=i)}T00:00:00Z", count=c, share=0.0)
        for i, c in enumerate(counts)
    ]


def growing() -> list[KeywordDynamicsPoint]:
    """A year flat at 100, then the last 4 weeks at 150: +50 % over the 4 before and the year."""
    return weekly([100] * 56 + [150] * 4)


def seasonal() -> list[KeywordDynamicsPoint]:
    """Flat now, but a year ago the 3 weeks after these doubled."""
    counts = [100] * 60
    counts[-52:-49] = [200, 200, 200]
    return weekly(counts)


def flat() -> list[KeywordDynamicsPoint]:
    return weekly([100] * 60)


class FakeWordstat:
    def __init__(
        self,
        tops: dict[str, TopRequests] | None = None,
        dynamics: dict[str, list[KeywordDynamicsPoint]] | None = None,
        *,
        rate_limited: bool = False,
    ) -> None:
        self.tops = tops or {}
        self.dynamics = dynamics or {}
        self.rate_limited = rate_limited
        self.calls: list[tuple[str, str]] = []

    async def top_requests(self, phrase: str, *, num_phrases: int = 20) -> TopRequests:
        self.calls.append(("top", phrase))
        if self.rate_limited:
            raise RateLimited()
        if phrase not in self.tops:
            raise YandexError(f"no top for {phrase!r}")
        return self.tops[phrase]

    async def keyword_dynamics(
        self, keyword: str, *, period: str, from_date: str, to_date: str
    ) -> list[KeywordDynamicsPoint]:
        assert period == "PERIOD_WEEKLY"
        assert date.fromisoformat(from_date[:10]).weekday() == 0, "fromDate must be a Monday"
        self.calls.append(("dynamics", keyword))
        if self.rate_limited:
            raise RateLimited()
        if keyword not in self.dynamics:
            raise YandexError(f"no dynamics for {keyword!r}")
        return self.dynamics[keyword]


class FakeLLM:
    """Answers the Карта Ниши prompt with `map_answer` and each Plan editor call with the next
    of `editor_answers` (an Exception is raised)."""

    def __init__(
        self, map_answer: str | Exception = "", editor_answers: list[str | Exception] | None = None
    ) -> None:
        self.map_answer = map_answer
        self.editor_answers = list(editor_answers or [])
        self.calls: list[list[Message]] = []

    async def complete_with_usage(
        self, messages: list[Message], *, temperature: float = 0.7
    ) -> Completion:
        self.calls.append(messages)
        system = messages[0].text
        if "Карту Ниши" in system:
            answer = self.map_answer
        elif "редактор контент-плана" in system:
            answer = self.editor_answers.pop(0)
        else:
            raise AssertionError(f"unexpected prompt: {system[:80]!r}")
        if isinstance(answer, Exception):
            raise answer
        return Completion(text=answer, model="yandexgpt/latest", tokens=100, cost=0.08)

    def editor_calls(self) -> list[list[Message]]:
        return [call for call in self.calls if "редактор контент-плана" in call[0].text]


class FakeNews:
    def __init__(self, hits: list[SearchHit] | None = None, error: Exception | None = None) -> None:
        self.hits = hits or []
        self.error = error
        self.calls: list[tuple[str, int, str | None]] = []

    async def search(self, query: str, *, limit: int, period: str | None = None) -> SearchResults:
        self.calls.append((query, limit, period))
        if self.error is not None:
            raise self.error
        return SearchResults(hits=self.hits, latency_ms=5, cost=0.49)


class FakeStore:
    """`WorkspaceSettingsStore` for several Пространства: Настройки and the Карта Ниши."""

    def __init__(self, values: dict[int, dict[str, str]]) -> None:
        self.values = values
        self.reads: list[tuple[int, str]] = []
        self.writes: list[tuple[int, str]] = []

    async def get(self, workspace_id: int, key: str) -> str | None:
        self.reads.append((workspace_id, key))
        return self.values.get(workspace_id, {}).get(key)

    async def set(self, workspace_id: int, key: str, value: str) -> None:
        self.writes.append((workspace_id, key))
        self.values.setdefault(workspace_id, {})[key] = value
