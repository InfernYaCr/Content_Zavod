from datetime import datetime, timezone

import pytest

from content_zavod.pipelines.plan_pipeline import make_generate_plan_handler
from content_zavod.yandex import KeywordDynamicsPoint, Message


class FakeKeywordStats:
    def __init__(self, dynamics: dict[str, list[KeywordDynamicsPoint]]) -> None:
        self._dynamics = dynamics
        self.calls: list[str] = []

    async def keyword_dynamics(
        self, keyword: str, *, period: str, from_date: str, to_date: str
    ) -> list[KeywordDynamicsPoint]:
        self.calls.append(keyword)
        if keyword not in self._dynamics:
            raise RuntimeError(f"no dynamics for {keyword!r}")
        return self._dynamics[keyword]


class FakeTextGenerator:
    def __init__(self, responses: dict[str, str]) -> None:
        self._responses = responses
        self.calls: list[list[Message]] = []

    async def complete(self, messages: list[Message], *, temperature: float = 0.7) -> str:
        self.calls.append(messages)
        user_text = messages[-1].text
        for keyword, response in self._responses.items():
            if keyword in user_text:
                return response
        raise AssertionError(f"no fake response configured for prompt: {user_text!r}")


def _growing(start: int, end: int) -> list[KeywordDynamicsPoint]:
    return [
        KeywordDynamicsPoint(date="2026-01-01T00:00:00Z", count=start, share=0.1),
        KeywordDynamicsPoint(date="2026-06-01T00:00:00Z", count=end, share=0.1),
    ]


async def _no_recent_titles(since: datetime) -> list[str]:
    return []


@pytest.mark.asyncio
async def test_handler_picks_growing_keywords_and_skips_declining_ones() -> None:
    keyword_stats = FakeKeywordStats(
        {
            "growing kw": _growing(100, 500),
            "declining kw": _growing(500, 100),
        }
    )
    text_generator = FakeTextGenerator(
        {"growing kw": "Title: Growing Topic\nSummary: it grows\nKeywords: growing kw, extra"}
    )
    handler = make_generate_plan_handler(
        keyword_stats,
        text_generator,
        _no_recent_titles,
        seed_keywords=("growing kw", "declining kw"),
        now=lambda: datetime(2026, 8, 7, tzinfo=timezone.utc),
    )

    output = await handler({"week_label": "Week 1"})

    assert output["week_label"] == "Week 1"
    assert output["topics"] == [
        {"title": "Growing Topic", "summary": "it grows", "keywords": ["growing kw", "extra"]}
    ]


@pytest.mark.asyncio
async def test_handler_ranks_by_growth_and_respects_topics_per_plan() -> None:
    keyword_stats = FakeKeywordStats(
        {
            "small growth": _growing(100, 120),
            "big growth": _growing(100, 1000),
        }
    )
    text_generator = FakeTextGenerator(
        {
            "small growth": "Title: Small\nSummary: s\nKeywords: small growth",
            "big growth": "Title: Big\nSummary: b\nKeywords: big growth",
        }
    )
    handler = make_generate_plan_handler(
        keyword_stats,
        text_generator,
        _no_recent_titles,
        seed_keywords=("small growth", "big growth"),
        topics_per_plan=1,
        now=lambda: datetime(2026, 8, 7, tzinfo=timezone.utc),
    )

    output = await handler({"week_label": "Week 1"})

    assert [t["title"] for t in output["topics"]] == ["Big"]


@pytest.mark.asyncio
async def test_handler_skips_a_topic_whose_title_was_already_used_recently() -> None:
    keyword_stats = FakeKeywordStats({"growing kw": _growing(100, 500)})
    text_generator = FakeTextGenerator(
        {"growing kw": "Title: Repeat Topic\nSummary: s\nKeywords: growing kw"}
    )

    async def recent_titles(since: datetime) -> list[str]:
        return ["repeat topic"]

    handler = make_generate_plan_handler(
        keyword_stats,
        text_generator,
        recent_titles,
        seed_keywords=("growing kw",),
        now=lambda: datetime(2026, 8, 7, tzinfo=timezone.utc),
    )

    output = await handler({"week_label": "Week 1"})

    assert output["topics"] == []


@pytest.mark.asyncio
async def test_handler_swallows_a_keyword_dynamics_failure_for_one_keyword() -> None:
    keyword_stats = FakeKeywordStats({"ok kw": _growing(100, 500)})
    text_generator = FakeTextGenerator({"ok kw": "Title: Ok Topic\nSummary: s\nKeywords: ok kw"})
    handler = make_generate_plan_handler(
        keyword_stats,
        text_generator,
        _no_recent_titles,
        seed_keywords=("broken kw", "ok kw"),
        now=lambda: datetime(2026, 8, 7, tzinfo=timezone.utc),
    )

    output = await handler({"week_label": "Week 1"})

    assert [t["title"] for t in output["topics"]] == ["Ok Topic"]


@pytest.mark.asyncio
async def test_handler_requests_a_six_month_monthly_dynamics_window() -> None:
    keyword_stats = FakeKeywordStats({})
    text_generator = FakeTextGenerator({})
    handler = make_generate_plan_handler(
        keyword_stats,
        text_generator,
        _no_recent_titles,
        seed_keywords=("missing kw",),
        now=lambda: datetime(2026, 8, 7, tzinfo=timezone.utc),
    )

    await handler({"week_label": "Week 1"})

    assert keyword_stats.calls == ["missing kw"]
