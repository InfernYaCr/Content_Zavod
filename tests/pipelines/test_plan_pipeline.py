from datetime import UTC, datetime

import pytest

from content_zavod.domain.plan import PlanItemDetail
from content_zavod.job_queue import JobPartialFailure
from content_zavod.pipelines.plan_pipeline import (
    make_generate_plan_handler,
    make_regenerate_topic_handler,
)
from content_zavod.settings import DEFAULT_DIRECTIONS, SettingsService
from content_zavod.yandex import Completion, KeywordDynamicsPoint, Message


class FakePlanItemReader:
    def __init__(self, item: PlanItemDetail) -> None:
        self._item = item

    async def get_item(self, plan_item_id: str) -> PlanItemDetail:
        assert plan_item_id == self._item.id
        return self._item


class FakeOwnerSettingsStore:
    def __init__(self, niche: str | None = None, directions: str | None = None) -> None:
        self._niche = niche
        self._directions = directions

    async def get(self, key: str) -> str | None:
        if key == "niche":
            return self._niche
        if key == "directions":
            return self._directions
        return None

    def set_niche(self, niche: str | None) -> None:
        self._niche = niche


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

    async def complete_with_usage(
        self, messages: list[Message], *, temperature: float = 0.7
    ) -> Completion:
        self.calls.append(messages)
        user_text = messages[-1].text
        for keyword, response in self._responses.items():
            if keyword in user_text:
                return Completion(text=response, model="yandexgpt/latest", tokens=10)
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
        SettingsService(FakeOwnerSettingsStore()),
        seed_keywords=("growing kw", "declining kw"),
        now=lambda: datetime(2026, 8, 7, tzinfo=UTC),
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
        SettingsService(FakeOwnerSettingsStore()),
        seed_keywords=("small growth", "big growth"),
        topics_per_plan=1,
        now=lambda: datetime(2026, 8, 7, tzinfo=UTC),
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
        SettingsService(FakeOwnerSettingsStore()),
        seed_keywords=("growing kw",),
        now=lambda: datetime(2026, 8, 7, tzinfo=UTC),
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
        SettingsService(FakeOwnerSettingsStore()),
        seed_keywords=("broken kw", "ok kw"),
        now=lambda: datetime(2026, 8, 7, tzinfo=UTC),
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
        SettingsService(FakeOwnerSettingsStore()),
        seed_keywords=("missing kw",),
        now=lambda: datetime(2026, 8, 7, tzinfo=UTC),
    )

    await handler({"week_label": "Week 1"})

    assert keyword_stats.calls == ["missing kw"]


@pytest.mark.asyncio
async def test_handler_uses_default_niche_when_unset() -> None:
    keyword_stats = FakeKeywordStats({"growing kw": _growing(100, 500)})
    text_generator = FakeTextGenerator(
        {"growing kw": "Title: Growing Topic\nSummary: it grows\nKeywords: growing kw"}
    )
    handler = make_generate_plan_handler(
        keyword_stats,
        text_generator,
        _no_recent_titles,
        SettingsService(FakeOwnerSettingsStore(None)),
        seed_keywords=("growing kw",),
        now=lambda: datetime(2026, 8, 7, tzinfo=UTC),
    )

    await handler({"week_label": "Week 1"})

    system_message = text_generator.calls[0][0]
    assert "«маркетинг»" in system_message.text


@pytest.mark.asyncio
async def test_handler_reads_niche_from_owner_settings_store() -> None:
    keyword_stats = FakeKeywordStats({"growing kw": _growing(100, 500)})
    text_generator = FakeTextGenerator(
        {"growing kw": "Title: Growing Topic\nSummary: it grows\nKeywords: growing kw"}
    )
    handler = make_generate_plan_handler(
        keyword_stats,
        text_generator,
        _no_recent_titles,
        SettingsService(FakeOwnerSettingsStore("edtech")),
        seed_keywords=("growing kw",),
        now=lambda: datetime(2026, 8, 7, tzinfo=UTC),
    )

    await handler({"week_label": "Week 1"})

    system_message = text_generator.calls[0][0]
    assert "«edtech»" in system_message.text


@pytest.mark.asyncio
async def test_handler_reads_niche_fresh_on_each_run_without_being_rebuilt() -> None:
    keyword_stats = FakeKeywordStats({"growing kw": _growing(100, 500)})
    text_generator = FakeTextGenerator(
        {"growing kw": "Title: Growing Topic\nSummary: it grows\nKeywords: growing kw"}
    )
    store = FakeOwnerSettingsStore("edtech")
    handler = make_generate_plan_handler(
        keyword_stats,
        text_generator,
        _no_recent_titles,
        SettingsService(store),
        seed_keywords=("growing kw",),
        now=lambda: datetime(2026, 8, 7, tzinfo=UTC),
    )

    await handler({"week_label": "Week 1"})
    assert "«edtech»" in text_generator.calls[0][0].text

    store._niche = "b2b saas"
    await handler({"week_label": "Week 2"})
    assert "«b2b saas»" in text_generator.calls[-1][0].text


@pytest.mark.asyncio
async def test_handler_uses_default_directions_when_seed_keywords_and_store_are_unset() -> None:
    keyword_stats = FakeKeywordStats({})
    text_generator = FakeTextGenerator({})
    handler = make_generate_plan_handler(
        keyword_stats,
        text_generator,
        _no_recent_titles,
        SettingsService(FakeOwnerSettingsStore()),
        now=lambda: datetime(2026, 8, 7, tzinfo=UTC),
    )

    await handler({"week_label": "Week 1"})

    assert keyword_stats.calls == list(DEFAULT_DIRECTIONS)


@pytest.mark.asyncio
async def test_handler_reads_directions_from_owner_settings_store() -> None:
    keyword_stats = FakeKeywordStats({"новая ниша": _growing(100, 500)})
    text_generator = FakeTextGenerator(
        {"новая ниша": "Title: New Topic\nSummary: s\nKeywords: новая ниша"}
    )
    handler = make_generate_plan_handler(
        keyword_stats,
        text_generator,
        _no_recent_titles,
        SettingsService(FakeOwnerSettingsStore(directions="новая ниша")),
        now=lambda: datetime(2026, 8, 7, tzinfo=UTC),
    )

    output = await handler({"week_label": "Week 1"})

    assert keyword_stats.calls == ["новая ниша"]
    assert [t["title"] for t in output["topics"]] == ["New Topic"]


@pytest.mark.asyncio
async def test_regenerate_topic_handler_redrafts_using_current_item_and_comment() -> None:
    current = PlanItemDetail(
        id="item-1", title="Old Title", summary="old summary", keywords=["old kw"]
    )
    item_reader = FakePlanItemReader(current)
    text_generator = FakeTextGenerator(
        {"Old Title": "Title: New Title\nSummary: new summary\nKeywords: new kw"}
    )
    handler = make_regenerate_topic_handler(
        item_reader, text_generator, SettingsService(FakeOwnerSettingsStore())
    )

    output = await handler({"plan_item_id": "item-1", "comment": "make it punchier"})

    assert output["plan_item_id"] == "item-1"
    assert output["title"] == "New Title"
    assert output["summary"] == "new summary"
    assert output["keywords"] == ["new kw"]
    assert [step["step_name"] for step in output["steps"]] == ["topic_regenerate"]
    assert "make it punchier" in text_generator.calls[0][-1].text


@pytest.mark.asyncio
async def test_regenerate_topic_handler_works_without_a_comment() -> None:
    current = PlanItemDetail(id="item-1", title="Old Title", summary="", keywords=[])
    item_reader = FakePlanItemReader(current)
    text_generator = FakeTextGenerator({"Old Title": "Title: New Title\nSummary: \nKeywords: "})
    handler = make_regenerate_topic_handler(
        item_reader, text_generator, SettingsService(FakeOwnerSettingsStore())
    )

    output = await handler({"plan_item_id": "item-1", "comment": None})

    assert output["title"] == "New Title"


@pytest.mark.asyncio
async def test_regenerate_topic_handler_reads_niche_from_owner_settings_store() -> None:
    current = PlanItemDetail(id="item-1", title="Old Title", summary="", keywords=[])
    item_reader = FakePlanItemReader(current)
    text_generator = FakeTextGenerator({"Old Title": "Title: New Title\nSummary: \nKeywords: "})
    handler = make_regenerate_topic_handler(
        item_reader, text_generator, SettingsService(FakeOwnerSettingsStore("edtech"))
    )

    await handler({"plan_item_id": "item-1", "comment": None})

    system_message = text_generator.calls[0][0]
    assert "«edtech»" in system_message.text


@pytest.mark.asyncio
async def test_regenerate_topic_handler_reads_niche_fresh_on_each_run_without_being_rebuilt() -> (
    None
):
    current = PlanItemDetail(id="item-1", title="Old Title", summary="", keywords=[])
    item_reader = FakePlanItemReader(current)
    text_generator = FakeTextGenerator({"Old Title": "Title: New Title\nSummary: \nKeywords: "})
    store = FakeOwnerSettingsStore("edtech")
    handler = make_regenerate_topic_handler(item_reader, text_generator, SettingsService(store))

    await handler({"plan_item_id": "item-1", "comment": None})
    assert "«edtech»" in text_generator.calls[0][0].text

    store._niche = "b2b saas"
    await handler({"plan_item_id": "item-1", "comment": None})
    assert "«b2b saas»" in text_generator.calls[-1][0].text


@pytest.mark.asyncio
async def test_handler_reports_step_provenance_per_topic_draft() -> None:
    keyword_stats = FakeKeywordStats(
        {"kw1": _growing(100, 500), "kw2": _growing(100, 500)},
    )
    text_generator = FakeTextGenerator(
        {
            "kw1": "Title: T1\nSummary: s\nKeywords: kw1",
            "kw2": "Title: T2\nSummary: s\nKeywords: kw2",
        }
    )
    handler = make_generate_plan_handler(
        keyword_stats,
        text_generator,
        _no_recent_titles,
        SettingsService(FakeOwnerSettingsStore()),
        seed_keywords=("kw1", "kw2"),
        topics_per_plan=2,
        now=lambda: datetime(2026, 8, 7, tzinfo=UTC),
    )

    output = await handler({"week_label": "Week 1"})

    assert len(output["steps"]) == 2
    assert {step["step_name"] for step in output["steps"]} == {"topic_draft"}


class _FailingTextGenerator:
    async def complete_with_usage(
        self, messages: list[Message], *, temperature: float = 0.7
    ) -> Completion:
        raise RuntimeError("model unavailable")


@pytest.mark.asyncio
async def test_regenerate_topic_handler_failure_raises_partial_failure() -> None:
    current = PlanItemDetail(id="item-1", title="Old Title", summary="", keywords=[])
    handler = make_regenerate_topic_handler(
        FakePlanItemReader(current),
        _FailingTextGenerator(),
        SettingsService(FakeOwnerSettingsStore()),
    )

    with pytest.raises(JobPartialFailure) as excinfo:
        await handler({"plan_item_id": "item-1", "comment": None})

    assert excinfo.value.partial_output["plan_item_id"] == "item-1"
    assert excinfo.value.partial_output["steps"] == []
