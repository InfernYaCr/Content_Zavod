"""Unit tests for the approve_all fan-out (`_generate_articles_for_approved_plan`, #14/#15)."""

from __future__ import annotations

from content_zavod.domain import PLATFORMS, PlanId, PlanItemId
from content_zavod.domain.plan import PlanItemDetail
from content_zavod.telegram import TelegramGateway
from content_zavod.telegram.callback_dispatcher import _generate_articles_for_approved_plan

CHAT_ID = 1


class FakeBot:
    def __init__(self) -> None:
        self.sent_messages: list[tuple[int, str, object]] = []

    async def send_message(self, chat_id, text, reply_markup=None) -> int:
        self.sent_messages.append((chat_id, text, reply_markup))
        return len(self.sent_messages)


class FakePlan:
    def __init__(self, items: list[PlanItemDetail]) -> None:
        self._items = items
        self.cover_requests: list[PlanItemId] = []
        self.started_batches: list[tuple[PlanId, int]] = []
        self.recorded_progress_refs: list[tuple[PlanId, int, int]] = []

    async def approved_items(self, plan_id: PlanId) -> list[PlanItemDetail]:
        return self._items

    async def request_cover(self, plan_item_id: PlanItemId) -> None:
        self.cover_requests.append(plan_item_id)

    async def start_generation_batch(self, plan_id: PlanId, total: int) -> bool:
        self.started_batches.append((plan_id, total))
        return True

    async def record_progress_message_ref(
        self, plan_id: PlanId, chat_id: int, message_id: int
    ) -> None:
        self.recorded_progress_refs.append((plan_id, chat_id, message_id))


class FakeArticle:
    def __init__(self) -> None:
        self.requested: list[tuple] = []

    async def request_generation(
        self,
        plan_id: PlanId,
        plan_item_id: PlanItemId,
        title: str,
        summary: str,
        keywords: list[str],
        platform: str,
    ) -> str:
        self.requested.append((plan_id, plan_item_id, title, summary, keywords, platform))
        return f"article-{plan_item_id}-{platform}"


async def test_requests_generation_for_every_approved_item_and_platform() -> None:
    items = [
        PlanItemDetail(id=PlanItemId("item-1"), title="Topic A", summary="s1", keywords=["k1"]),
        PlanItemDetail(id=PlanItemId("item-2"), title="Topic B", summary="s2", keywords=["k2"]),
    ]
    plan, article = FakePlan(items), FakeArticle()
    gateway = TelegramGateway(FakeBot())

    await _generate_articles_for_approved_plan(plan, article, gateway, CHAT_ID, PlanId("plan-1"))

    assert article.requested == [
        (PlanId("plan-1"), PlanItemId("item-1"), "Topic A", "s1", ["k1"], platform)
        for platform in PLATFORMS
    ] + [
        (PlanId("plan-1"), PlanItemId("item-2"), "Topic B", "s2", ["k2"], platform)
        for platform in PLATFORMS
    ]


async def test_requests_one_cover_per_approved_item_regardless_of_platform_count() -> None:
    items = [
        PlanItemDetail(id=PlanItemId("item-1"), title="Topic A", summary="s1", keywords=["k1"]),
        PlanItemDetail(id=PlanItemId("item-2"), title="Topic B", summary="s2", keywords=["k2"]),
    ]
    plan, article = FakePlan(items), FakeArticle()
    gateway = TelegramGateway(FakeBot())

    await _generate_articles_for_approved_plan(plan, article, gateway, CHAT_ID, PlanId("plan-1"))

    assert plan.cover_requests == [PlanItemId("item-1"), PlanItemId("item-2")]


async def test_no_approved_items_means_no_generation_requested() -> None:
    plan, article = FakePlan([]), FakeArticle()
    gateway = TelegramGateway(FakeBot())

    await _generate_articles_for_approved_plan(plan, article, gateway, CHAT_ID, PlanId("plan-1"))

    assert article.requested == []
    assert plan.cover_requests == []
