"""Unit tests for «Утвердить всё» (`approve_and_generate`, #14/#15, ADR-0015) on fakes. The
same on a real database - race, retry, charge-once - is in `tests/workspaces/test_limits.py`."""

from __future__ import annotations

from typing import Any, cast

import pytest

from content_zavod.domain import PLATFORMS, PlanId, PlanItemId
from content_zavod.domain.plan import PlanItemDetail
from content_zavod.telegram.callback_dispatcher import approve_and_generate
from content_zavod.workspaces import TrialLimitExhausted, Usage
from tests.telegram.fakes import FakeLimits
from tests.tenancy import WS_A


class FakePlan:
    def __init__(self, items: list[PlanItemDetail], *, approved: bool = False) -> None:
        self._items = items
        self.approved = approved
        self.covers_enabled = True
        self.cover_requests: list[PlanItemId] = []

    async def approve_all(self, workspace_id, plan_id: PlanId, *, conn=None) -> bool:
        if self.approved:
            return False
        self.approved = True
        return True

    async def approved_items(self, workspace_id, plan_id: PlanId, *, conn=None):
        return self._items

    async def request_cover(self, workspace_id, plan_item_id: PlanItemId, *, conn=None) -> None:
        self.cover_requests.append(plan_item_id)


class FakeArticle:
    def __init__(self) -> None:
        self.requested: list[tuple] = []

    async def request_generation(
        self, workspace_id, plan_id, plan_item_id, title, summary, keywords, platform, *, conn=None
    ) -> str:
        self.requested.append((plan_id, plan_item_id, title, summary, keywords, platform))
        return f"article-{plan_item_id}-{platform}"


ITEMS = [
    PlanItemDetail(id=PlanItemId("item-1"), title="Topic A", summary="s1", keywords=["k1"]),
    PlanItemDetail(id=PlanItemId("item-2"), title="Topic B", summary="s2", keywords=["k2"]),
]


async def approve(plan: FakePlan, article: FakeArticle, limits: FakeLimits) -> None:
    await approve_and_generate(
        cast(Any, limits), cast(Any, plan), cast(Any, article), WS_A, PlanId("plan-1")
    )


async def test_requests_generation_for_every_approved_item_and_platform() -> None:
    plan, article, limits = FakePlan(ITEMS), FakeArticle(), FakeLimits()

    await approve(plan, article, limits)

    assert article.requested == [
        (PlanId("plan-1"), PlanItemId("item-1"), "Topic A", "s1", ["k1"], platform)
        for platform in PLATFORMS
    ] + [
        (PlanId("plan-1"), PlanItemId("item-2"), "Topic B", "s2", ["k2"], platform)
        for platform in PLATFORMS
    ]
    assert plan.cover_requests == [PlanItemId("item-1"), PlanItemId("item-2")]


async def test_the_whole_set_of_statyi_is_checked_against_the_limit_at_once() -> None:
    plan, article, limits = FakePlan(ITEMS), FakeArticle(), FakeLimits()

    await approve(plan, article, limits)

    assert limits.reserved == [(None, 1)]
    assert limits.required == [
        ("generate_article", len(ITEMS) * len(PLATFORMS)),
        ("generate_cover", len(ITEMS)),
    ]


async def test_an_already_approved_plan_starts_nothing_again() -> None:
    plan, article, limits = FakePlan(ITEMS, approved=True), FakeArticle(), FakeLimits()

    await approve(plan, article, limits)

    assert article.requested == [] and plan.cover_requests == []
    assert limits.required == []


async def test_short_of_the_limit_nothing_is_requested() -> None:
    plan, article, limits = FakePlan(ITEMS), FakeArticle(), FakeLimits()
    usage = Usage(plans_used=1, plan_limit=1, articles_used=5, article_limit=8)
    limits.short = TrialLimitExhausted(usage, plans=0, articles=4, contact=None)

    with pytest.raises(TrialLimitExhausted):
        await approve(plan, article, limits)

    assert article.requested == [] and plan.cover_requests == []
