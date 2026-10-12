import pytest

from content_zavod.telegram import PlanItemId, PlanReview
from content_zavod.telegram.pending_inputs import PendingInput
from tests.tenancy import WS_A

from .fakes import FakePendingInputs


class FakeOps:
    def __init__(self) -> None:
        self.deleted: list[PlanItemId] = []
        self.regenerated: list[tuple[PlanItemId, str | None]] = []
        self.approved: list[PlanItemId] = []

    async def delete_item(self, workspace_id: int, plan_item_id: PlanItemId) -> None:
        assert workspace_id == WS_A
        self.deleted.append(plan_item_id)

    async def regenerate_item(
        self, workspace_id: int, plan_item_id: PlanItemId, comment: str | None
    ) -> None:
        assert workspace_id == WS_A
        self.regenerated.append((plan_item_id, comment))

    async def approve_all(self, workspace_id: int, plan_id: str) -> None:
        assert workspace_id == WS_A
        self.approved.append(PlanItemId(plan_id))


class FakePrompt:
    def __init__(self) -> None:
        self.prompted: list[tuple[int, PlanItemId]] = []

    async def prompt_for_comment(self, chat_id: int, plan_item_id: PlanItemId) -> int:
        self.prompted.append((chat_id, plan_item_id))
        return 100

    async def mark_generating(self, chat_id: int, pending: PendingInput) -> None:
        pass

    async def withdraw(self, chat_id: int, pending: PendingInput) -> None:
        pass


@pytest.fixture
def ops() -> FakeOps:
    return FakeOps()


@pytest.fixture
def prompt() -> FakePrompt:
    return FakePrompt()


@pytest.fixture
def review(ops: FakeOps, prompt: FakePrompt) -> PlanReview:
    return PlanReview(ops, prompt, FakePendingInputs())


@pytest.mark.asyncio
async def test_delete_calls_ops_directly(review: PlanReview, ops: FakeOps) -> None:
    await review.handle_action(WS_A, 1, 10, PlanItemId("item-1"), "delete")

    assert ops.deleted == [PlanItemId("item-1")]
    assert ops.regenerated == []


@pytest.mark.asyncio
async def test_approve_all_calls_ops_directly(review: PlanReview, ops: FakeOps) -> None:
    await review.handle_action(WS_A, 1, 10, PlanItemId("plan-1"), "approve_all")

    assert ops.approved == [PlanItemId("plan-1")]


@pytest.mark.asyncio
async def test_regenerate_action_delegates_to_the_comment_gated_flow(
    review: PlanReview, ops: FakeOps, prompt: FakePrompt
) -> None:
    await review.handle_action(WS_A, 1, 10, PlanItemId("item-1"), "regenerate")

    assert prompt.prompted == [(1, PlanItemId("item-1"))]
    assert ops.regenerated == []

    consumed = await review.handle_comment_reply(WS_A, 1, 10, "please make it shorter")

    assert consumed is True
    assert ops.regenerated == [(PlanItemId("item-1"), "please make it shorter")]


@pytest.mark.asyncio
async def test_delete_clears_any_pending_wait_for_same_user(
    review: PlanReview, ops: FakeOps
) -> None:
    await review.handle_action(WS_A, 1, 10, PlanItemId("item-1"), "regenerate")

    await review.handle_action(WS_A, 1, 10, PlanItemId("item-2"), "delete")

    consumed = await review.handle_comment_reply(WS_A, 1, 10, "too late")
    assert consumed is False
    assert ops.deleted == [PlanItemId("item-2")]


@pytest.mark.asyncio
async def test_approve_all_clears_any_pending_wait_for_same_user(
    review: PlanReview, ops: FakeOps
) -> None:
    await review.handle_action(WS_A, 1, 10, PlanItemId("item-1"), "regenerate")

    await review.handle_action(WS_A, 1, 10, PlanItemId("plan-1"), "approve_all")

    consumed = await review.handle_comment_reply(WS_A, 1, 10, "too late")
    assert consumed is False
    assert ops.approved == [PlanItemId("plan-1")]


@pytest.mark.asyncio
async def test_will_enqueue_regeneration_is_false_before_first_press(review: PlanReview) -> None:
    assert await review.will_enqueue_regeneration(WS_A, 1, 10, PlanItemId("item-1")) is False


@pytest.mark.asyncio
async def test_will_enqueue_regeneration_is_true_on_matching_second_press(
    review: PlanReview,
) -> None:
    await review.handle_action(WS_A, 1, 10, PlanItemId("item-1"), "regenerate")

    assert await review.will_enqueue_regeneration(WS_A, 1, 10, PlanItemId("item-1")) is True


@pytest.mark.asyncio
async def test_cancel_comment_drops_the_wait_for_that_item_only(
    review: PlanReview, ops: FakeOps
) -> None:
    await review.handle_action(WS_A, 1, 10, PlanItemId("item-1"), "regenerate")

    assert await review.cancel_comment(WS_A, 1, 10, PlanItemId("item-2")) is False
    assert await review.cancel_comment(WS_A, 1, 10, PlanItemId("item-1")) is True

    assert await review.handle_comment_reply(WS_A, 1, 10, "too late") is False
    assert ops.regenerated == []
