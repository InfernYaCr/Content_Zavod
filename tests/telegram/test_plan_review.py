import pytest

from content_zavod.telegram import PlanItemId, PlanReview


class FakeOps:
    def __init__(self) -> None:
        self.deleted: list[PlanItemId] = []
        self.regenerated: list[tuple[PlanItemId, str | None]] = []
        self.approved: list[PlanItemId] = []

    async def delete_item(self, plan_item_id: PlanItemId) -> None:
        self.deleted.append(plan_item_id)

    async def regenerate_item(self, plan_item_id: PlanItemId, comment: str | None) -> None:
        self.regenerated.append((plan_item_id, comment))

    async def approve_all(self, plan_item_id: PlanItemId) -> None:
        self.approved.append(plan_item_id)


class FakePrompt:
    def __init__(self) -> None:
        self.prompted: list[tuple[int, PlanItemId]] = []

    async def prompt_for_comment(self, chat_id: int, plan_item_id: PlanItemId) -> None:
        self.prompted.append((chat_id, plan_item_id))


@pytest.fixture
def ops() -> FakeOps:
    return FakeOps()


@pytest.fixture
def prompt() -> FakePrompt:
    return FakePrompt()


@pytest.fixture
def review(ops: FakeOps, prompt: FakePrompt) -> PlanReview:
    return PlanReview(ops, prompt)


@pytest.mark.asyncio
async def test_delete_calls_ops_directly(review: PlanReview, ops: FakeOps) -> None:
    await review.handle_action(1, 10, PlanItemId("item-1"), "delete")

    assert ops.deleted == [PlanItemId("item-1")]
    assert ops.regenerated == []


@pytest.mark.asyncio
async def test_approve_all_calls_ops_directly(review: PlanReview, ops: FakeOps) -> None:
    await review.handle_action(1, 10, PlanItemId("plan-1"), "approve_all")

    assert ops.approved == [PlanItemId("plan-1")]


@pytest.mark.asyncio
async def test_regenerate_action_delegates_to_the_comment_gated_flow(
    review: PlanReview, ops: FakeOps, prompt: FakePrompt
) -> None:
    await review.handle_action(1, 10, PlanItemId("item-1"), "regenerate")

    assert prompt.prompted == [(1, PlanItemId("item-1"))]
    assert ops.regenerated == []

    consumed = await review.handle_comment_reply(1, 10, "please make it shorter")

    assert consumed is True
    assert ops.regenerated == [(PlanItemId("item-1"), "please make it shorter")]


@pytest.mark.asyncio
async def test_delete_clears_any_pending_wait_for_same_user(
    review: PlanReview, ops: FakeOps
) -> None:
    await review.handle_action(1, 10, PlanItemId("item-1"), "regenerate")

    await review.handle_action(1, 10, PlanItemId("item-2"), "delete")

    consumed = await review.handle_comment_reply(1, 10, "too late")
    assert consumed is False
    assert ops.deleted == [PlanItemId("item-2")]


@pytest.mark.asyncio
async def test_approve_all_clears_any_pending_wait_for_same_user(
    review: PlanReview, ops: FakeOps
) -> None:
    await review.handle_action(1, 10, PlanItemId("item-1"), "regenerate")

    await review.handle_action(1, 10, PlanItemId("plan-1"), "approve_all")

    consumed = await review.handle_comment_reply(1, 10, "too late")
    assert consumed is False
    assert ops.approved == [PlanItemId("plan-1")]
