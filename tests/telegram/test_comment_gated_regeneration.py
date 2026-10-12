import pytest

from content_zavod.telegram import CommentGatedRegeneration
from content_zavod.telegram.comment_gated_regeneration import COMMENT_MAX_LENGTH
from content_zavod.telegram.pending_inputs import PendingInput
from tests.tenancy import WS_A, WS_B

from .fakes import FakePendingInputs


class FakeRegenerate:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str | None]] = []

    async def __call__(self, workspace_id: int, id_: str, comment: str | None) -> None:
        assert workspace_id == WS_A
        self.calls.append((id_, comment))


class FakePrompt:
    """Each prompt is one message: id 100 for the first, 101 for the next..."""

    def __init__(self) -> None:
        self.prompted: list[tuple[int, str]] = []
        self.generating: list[tuple[int, int]] = []
        self.withdrawn: list[tuple[int, int]] = []
        self.rejected: list[int] = []
        self._next_message_id = 100

    async def prompt_for_comment(self, chat_id: int, id_: str) -> int:
        self.prompted.append((chat_id, id_))
        self._next_message_id += 1
        return self._next_message_id - 1

    async def mark_generating(self, chat_id: int, pending: PendingInput) -> None:
        self.generating.append((chat_id, pending.prompt_message_id))

    async def withdraw(self, chat_id: int, pending: PendingInput) -> None:
        self.withdrawn.append((chat_id, pending.prompt_message_id))

    async def reject_too_long(self, chat_id: int) -> None:
        self.rejected.append(chat_id)


@pytest.fixture
def regenerate() -> FakeRegenerate:
    return FakeRegenerate()


@pytest.fixture
def prompt() -> FakePrompt:
    return FakePrompt()


@pytest.fixture
def pending() -> FakePendingInputs:
    return FakePendingInputs()


@pytest.fixture
def flow(
    regenerate: FakeRegenerate, prompt: FakePrompt, pending: FakePendingInputs
) -> CommentGatedRegeneration[str]:
    return CommentGatedRegeneration(regenerate, prompt, pending, kind="test_comment")


@pytest.mark.asyncio
async def test_first_press_prompts_for_comment_without_regenerating(
    flow: CommentGatedRegeneration[str], regenerate: FakeRegenerate, prompt: FakePrompt
) -> None:
    await flow.request(WS_A, 1, 10, "item-1")

    assert prompt.prompted == [(1, "item-1")]
    assert regenerate.calls == []


@pytest.mark.asyncio
async def test_comment_reply_resolves_pending_regenerate(
    flow: CommentGatedRegeneration[str], regenerate: FakeRegenerate, prompt: FakePrompt
) -> None:
    await flow.request(WS_A, 1, 10, "item-1")

    consumed = await flow.handle_comment_reply(WS_A, 1, 10, "please make it shorter")

    assert consumed is True
    assert regenerate.calls == [("item-1", "please make it shorter")]
    assert prompt.generating == [(1, 100)]


@pytest.mark.asyncio
async def test_comment_reply_without_pending_wait_is_ignored(
    flow: CommentGatedRegeneration[str],
) -> None:
    consumed = await flow.handle_comment_reply(WS_A, 1, 10, "stray text")

    assert consumed is False


@pytest.mark.asyncio
async def test_skip_button_repeats_same_target_and_regenerates_without_comment(
    flow: CommentGatedRegeneration[str], regenerate: FakeRegenerate, prompt: FakePrompt
) -> None:
    await flow.request(WS_A, 1, 10, "item-1")
    await flow.request(WS_A, 1, 10, "item-1")

    assert regenerate.calls == [("item-1", None)]
    assert prompt.prompted == [(1, "item-1")]  # only prompted once
    # #80: the prompt's own request message (100) shows "generating", not the pressed one.
    assert prompt.generating == [(1, 100)]


@pytest.mark.asyncio
async def test_new_press_on_different_target_silently_cancels_previous_wait(
    flow: CommentGatedRegeneration[str], regenerate: FakeRegenerate, prompt: FakePrompt
) -> None:
    await flow.request(WS_A, 1, 10, "item-1")
    await flow.request(WS_A, 1, 10, "item-2")

    assert prompt.prompted == [(1, "item-1"), (1, "item-2")]
    assert regenerate.calls == []

    consumed = await flow.handle_comment_reply(WS_A, 1, 10, "comment for item-2")
    assert consumed is True
    assert regenerate.calls == [("item-2", "comment for item-2")]


@pytest.mark.asyncio
async def test_a_superseded_wait_has_its_prompt_withdrawn(
    flow: CommentGatedRegeneration[str], prompt: FakePrompt
) -> None:
    """The earlier prompt's buttons would be dead - its messages are removed, not left behind."""
    await flow.request(WS_A, 1, 10, "item-1")  # message 100
    await flow.request(WS_A, 1, 10, "item-2")  # message 101

    assert prompt.withdrawn == [(1, 100)]
    assert prompt.generating == []


@pytest.mark.asyncio
async def test_pending_wait_is_scoped_per_chat_and_user(
    flow: CommentGatedRegeneration[str], regenerate: FakeRegenerate
) -> None:
    await flow.request(WS_A, 1, 10, "item-1")

    consumed = await flow.handle_comment_reply(WS_A, 1, 99, "wrong user")
    assert consumed is False

    consumed = await flow.handle_comment_reply(WS_A, 2, 10, "wrong chat")
    assert consumed is False

    consumed = await flow.handle_comment_reply(WS_A, 1, 10, "right one")
    assert consumed is True
    assert regenerate.calls == [("item-1", "right one")]


@pytest.mark.asyncio
async def test_wait_survives_a_restart_through_the_store(
    regenerate: FakeRegenerate, prompt: FakePrompt, pending: FakePendingInputs
) -> None:
    """#88: the wait lives in the store, not the flow object - a fresh flow (a restarted bot)
    over the same store still resolves it."""
    before = CommentGatedRegeneration(regenerate, prompt, pending, kind="test_comment")
    await before.request(WS_A, 1, 10, "item-1")

    after = CommentGatedRegeneration(regenerate, prompt, pending, kind="test_comment")
    consumed = await after.handle_comment_reply(WS_A, 1, 10, "comment")

    assert consumed is True
    assert regenerate.calls == [("item-1", "comment")]


@pytest.mark.asyncio
async def test_a_flow_ignores_another_kinds_wait(
    regenerate: FakeRegenerate, prompt: FakePrompt, pending: FakePendingInputs
) -> None:
    plan_flow = CommentGatedRegeneration(regenerate, prompt, pending, kind="plan_item_comment")
    article_flow = CommentGatedRegeneration(regenerate, prompt, pending, kind="article_comment")
    await article_flow.request(WS_A, 1, 10, "article-1")

    assert await plan_flow.handle_comment_reply(WS_A, 1, 10, "comment") is False
    assert await article_flow.handle_comment_reply(WS_A, 1, 10, "comment") is True
    assert regenerate.calls == [("article-1", "comment")]


@pytest.mark.asyncio
async def test_cancel_clears_pending_wait_and_withdraws_its_prompt(
    flow: CommentGatedRegeneration[str], regenerate: FakeRegenerate, prompt: FakePrompt
) -> None:
    await flow.request(WS_A, 1, 10, "item-1")

    assert await flow.cancel(WS_A, 1, 10) is True
    assert prompt.withdrawn == [(1, 100)]

    consumed = await flow.handle_comment_reply(WS_A, 1, 10, "too late")
    assert consumed is False
    assert regenerate.calls == []


@pytest.mark.asyncio
async def test_cancel_without_pending_wait_is_a_no_op(flow: CommentGatedRegeneration[str]) -> None:
    assert await flow.cancel(WS_A, 1, 10) is False


@pytest.mark.asyncio
async def test_cancel_for_a_stale_target_keeps_the_newer_wait(
    flow: CommentGatedRegeneration[str], regenerate: FakeRegenerate
) -> None:
    await flow.request(WS_A, 1, 10, "item-1")
    await flow.request(WS_A, 1, 10, "item-2")

    assert await flow.cancel(WS_A, 1, 10, "item-1") is False

    assert await flow.handle_comment_reply(WS_A, 1, 10, "comment") is True
    assert regenerate.calls == [("item-2", "comment")]


@pytest.mark.asyncio
async def test_has_matching_pending_is_false_with_no_pending_wait(
    flow: CommentGatedRegeneration[str],
) -> None:
    assert await flow.has_matching_pending(WS_A, 1, 10, "item-1") is False


@pytest.mark.asyncio
async def test_has_matching_pending_is_true_for_a_matching_second_press(
    flow: CommentGatedRegeneration[str],
) -> None:
    await flow.request(WS_A, 1, 10, "item-1")

    assert await flow.has_matching_pending(WS_A, 1, 10, "item-1") is True


@pytest.mark.asyncio
async def test_has_matching_pending_is_false_for_a_different_target(
    flow: CommentGatedRegeneration[str],
) -> None:
    await flow.request(WS_A, 1, 10, "item-1")

    assert await flow.has_matching_pending(WS_A, 1, 10, "item-2") is False


@pytest.mark.asyncio
async def test_a_text_in_another_workspace_does_not_close_the_wait(
    flow: CommentGatedRegeneration[str], regenerate: FakeRegenerate
) -> None:
    """ADR-0015: the same chat and user ids in Workspace B can't resolve A's wait."""
    await flow.request(WS_A, 1, 10, "item-1")

    assert await flow.handle_comment_reply(WS_B, 1, 10, "чужой") is False
    assert await flow.cancel(WS_B, 1, 10) is False
    assert await flow.has_matching_pending(WS_B, 1, 10, "item-1") is False
    assert await flow.handle_comment_reply(WS_A, 1, 10, "свой") is True
    assert regenerate.calls == [("item-1", "свой")]


@pytest.mark.asyncio
async def test_a_long_text_without_a_wait_is_left_to_other_handlers(
    flow: CommentGatedRegeneration[str], prompt: FakePrompt
) -> None:
    assert not await flow.handle_comment_reply(WS_A, 1, 10, "я" * (COMMENT_MAX_LENGTH + 1))
    assert prompt.rejected == []


@pytest.mark.asyncio
async def test_the_comment_reaches_the_regeneration_trimmed(
    flow: CommentGatedRegeneration[str], regenerate: FakeRegenerate
) -> None:
    await flow.request(WS_A, 1, 10, "item-1")

    await flow.handle_comment_reply(WS_A, 1, 10, "  короче \n")

    assert regenerate.calls == [("item-1", "короче")]
