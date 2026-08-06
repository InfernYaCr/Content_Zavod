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
async def test_regenerate_first_press_prompts_for_comment_without_calling_ops(
    review: PlanReview, ops: FakeOps, prompt: FakePrompt
) -> None:
    await review.handle_action(1, 10, PlanItemId("item-1"), "regenerate")

    assert prompt.prompted == [(1, PlanItemId("item-1"))]
    assert ops.regenerated == []


@pytest.mark.asyncio
async def test_comment_reply_resolves_pending_regenerate(
    review: PlanReview, ops: FakeOps
) -> None:
    await review.handle_action(1, 10, PlanItemId("item-1"), "regenerate")

    consumed = await review.handle_comment_reply(1, 10, "please make it shorter")

    assert consumed is True
    assert ops.regenerated == [(PlanItemId("item-1"), "please make it shorter")]


@pytest.mark.asyncio
async def test_comment_reply_without_pending_wait_is_ignored(review: PlanReview) -> None:
    consumed = await review.handle_comment_reply(1, 10, "stray text")

    assert consumed is False


@pytest.mark.asyncio
async def test_skip_button_repeats_same_action_and_regenerates_without_comment(
    review: PlanReview, ops: FakeOps, prompt: FakePrompt
) -> None:
    await review.handle_action(1, 10, PlanItemId("item-1"), "regenerate")
    await review.handle_action(1, 10, PlanItemId("item-1"), "regenerate")

    assert ops.regenerated == [(PlanItemId("item-1"), None)]
    assert prompt.prompted == [(1, PlanItemId("item-1"))]  # only prompted once


@pytest.mark.asyncio
async def test_new_regenerate_press_on_different_item_silently_cancels_previous_wait(
    review: PlanReview, ops: FakeOps, prompt: FakePrompt
) -> None:
    await review.handle_action(1, 10, PlanItemId("item-1"), "regenerate")
    await review.handle_action(1, 10, PlanItemId("item-2"), "regenerate")

    assert prompt.prompted == [(1, PlanItemId("item-1")), (1, PlanItemId("item-2"))]
    assert ops.regenerated == []

    consumed = await review.handle_comment_reply(1, 10, "comment for item-2")
    assert consumed is True
    assert ops.regenerated == [(PlanItemId("item-2"), "comment for item-2")]


@pytest.mark.asyncio
async def test_pending_wait_is_scoped_per_chat_and_user(
    review: PlanReview, ops: FakeOps
) -> None:
    await review.handle_action(1, 10, PlanItemId("item-1"), "regenerate")

    consumed = await review.handle_comment_reply(1, 99, "wrong user")
    assert consumed is False

    consumed = await review.handle_comment_reply(2, 10, "wrong chat")
    assert consumed is False

    consumed = await review.handle_comment_reply(1, 10, "right one")
    assert consumed is True
    assert ops.regenerated == [(PlanItemId("item-1"), "right one")]


@pytest.mark.asyncio
async def test_delete_clears_any_pending_wait_for_same_user(
    review: PlanReview, ops: FakeOps
) -> None:
    await review.handle_action(1, 10, PlanItemId("item-1"), "regenerate")

    await review.handle_action(1, 10, PlanItemId("item-2"), "delete")

    consumed = await review.handle_comment_reply(1, 10, "too late")
    assert consumed is False
    assert ops.deleted == [PlanItemId("item-2")]
