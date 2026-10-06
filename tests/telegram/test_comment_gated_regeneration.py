import pytest

from content_zavod.telegram import CommentGatedRegeneration

from .fakes import FakePendingInputs


class FakeRegenerate:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str | None]] = []

    async def __call__(self, id_: str, comment: str | None) -> None:
        self.calls.append((id_, comment))


class FakePrompt:
    """Each prompt is two messages: ids 100/101 for the first, 102/103 for the next..."""

    def __init__(self) -> None:
        self.prompted: list[tuple[int, str]] = []
        self.generating: list[tuple[int, int]] = []
        self._next_message_id = 100

    async def prompt_for_comment(self, chat_id: int, user_id: int, id_: str) -> tuple[int, int]:
        self.prompted.append((chat_id, id_))
        self._next_message_id += 2
        return self._next_message_id - 2, self._next_message_id - 1

    async def mark_generating(self, chat_id: int, prompt_message_id: int) -> None:
        self.generating.append((chat_id, prompt_message_id))


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
    await flow.request(1, 10, "item-1")

    assert prompt.prompted == [(1, "item-1")]
    assert regenerate.calls == []


@pytest.mark.asyncio
async def test_comment_reply_resolves_pending_regenerate(
    flow: CommentGatedRegeneration[str], regenerate: FakeRegenerate, prompt: FakePrompt
) -> None:
    await flow.request(1, 10, "item-1")

    consumed = await flow.handle_comment_reply(1, 10, "please make it shorter", None)

    assert consumed is True
    assert regenerate.calls == [("item-1", "please make it shorter")]
    assert prompt.generating == [(1, 100)]


@pytest.mark.asyncio
async def test_comment_reply_without_pending_wait_is_ignored(
    flow: CommentGatedRegeneration[str],
) -> None:
    consumed = await flow.handle_comment_reply(1, 10, "stray text", None)

    assert consumed is False


@pytest.mark.asyncio
async def test_skip_button_repeats_same_target_and_regenerates_without_comment(
    flow: CommentGatedRegeneration[str], regenerate: FakeRegenerate, prompt: FakePrompt
) -> None:
    await flow.request(1, 10, "item-1")
    await flow.request(1, 10, "item-1")

    assert regenerate.calls == [("item-1", None)]
    assert prompt.prompted == [(1, "item-1")]  # only prompted once
    # #80: the prompt's own request message (100) shows "generating", not the pressed one.
    assert prompt.generating == [(1, 100)]


@pytest.mark.asyncio
async def test_new_press_on_different_target_silently_cancels_previous_wait(
    flow: CommentGatedRegeneration[str], regenerate: FakeRegenerate, prompt: FakePrompt
) -> None:
    await flow.request(1, 10, "item-1")
    await flow.request(1, 10, "item-2")

    assert prompt.prompted == [(1, "item-1"), (1, "item-2")]
    assert regenerate.calls == []

    consumed = await flow.handle_comment_reply(1, 10, "comment for item-2", None)
    assert consumed is True
    assert regenerate.calls == [("item-2", "comment for item-2")]


@pytest.mark.asyncio
async def test_pending_wait_is_scoped_per_chat_and_user(
    flow: CommentGatedRegeneration[str], regenerate: FakeRegenerate
) -> None:
    await flow.request(1, 10, "item-1")

    consumed = await flow.handle_comment_reply(1, 99, "wrong user", None)
    assert consumed is False

    consumed = await flow.handle_comment_reply(2, 10, "wrong chat", None)
    assert consumed is False

    consumed = await flow.handle_comment_reply(1, 10, "right one", None)
    assert consumed is True
    assert regenerate.calls == [("item-1", "right one")]


@pytest.mark.asyncio
async def test_in_a_group_only_a_reply_to_the_prompt_counts_as_the_comment(
    flow: CommentGatedRegeneration[str], regenerate: FakeRegenerate
) -> None:
    """#88: a reply to some other message (a colleague's, an old prompt) leaves the wait
    open; a reply to either of the prompt's two messages resolves it."""
    await flow.request(1, 10, "item-1")  # prompt messages 100 (buttons) and 101 (ForceReply)

    assert await flow.handle_comment_reply(1, 10, "ок, щас гляну", 55) is False
    assert regenerate.calls == []

    assert await flow.handle_comment_reply(1, 10, "короче", 101) is True
    assert regenerate.calls == [("item-1", "короче")]


@pytest.mark.asyncio
async def test_a_reply_to_the_buttons_message_also_counts(
    flow: CommentGatedRegeneration[str], regenerate: FakeRegenerate
) -> None:
    await flow.request(1, 10, "item-1")

    assert await flow.handle_comment_reply(1, 10, "короче", 100) is True
    assert regenerate.calls == [("item-1", "короче")]


@pytest.mark.asyncio
async def test_wait_survives_a_restart_through_the_store(
    regenerate: FakeRegenerate, prompt: FakePrompt, pending: FakePendingInputs
) -> None:
    """#88: the wait lives in the store, not the flow object - a fresh flow (a restarted bot)
    over the same store still resolves it."""
    before = CommentGatedRegeneration(regenerate, prompt, pending, kind="test_comment")
    await before.request(1, 10, "item-1")

    after = CommentGatedRegeneration(regenerate, prompt, pending, kind="test_comment")
    consumed = await after.handle_comment_reply(1, 10, "comment", None)

    assert consumed is True
    assert regenerate.calls == [("item-1", "comment")]


@pytest.mark.asyncio
async def test_a_flow_ignores_another_kinds_wait(
    regenerate: FakeRegenerate, prompt: FakePrompt, pending: FakePendingInputs
) -> None:
    plan_flow = CommentGatedRegeneration(regenerate, prompt, pending, kind="plan_item_comment")
    article_flow = CommentGatedRegeneration(regenerate, prompt, pending, kind="article_comment")
    await article_flow.request(1, 10, "article-1")

    assert await plan_flow.handle_comment_reply(1, 10, "comment", None) is False
    assert await article_flow.handle_comment_reply(1, 10, "comment", None) is True
    assert regenerate.calls == [("article-1", "comment")]


@pytest.mark.asyncio
async def test_cancel_clears_pending_wait_for_same_user(
    flow: CommentGatedRegeneration[str], regenerate: FakeRegenerate
) -> None:
    await flow.request(1, 10, "item-1")

    assert await flow.cancel(1, 10) is True

    consumed = await flow.handle_comment_reply(1, 10, "too late", None)
    assert consumed is False
    assert regenerate.calls == []


@pytest.mark.asyncio
async def test_cancel_without_pending_wait_is_a_no_op(flow: CommentGatedRegeneration[str]) -> None:
    assert await flow.cancel(1, 10) is False


@pytest.mark.asyncio
async def test_cancel_for_a_stale_target_keeps_the_newer_wait(
    flow: CommentGatedRegeneration[str], regenerate: FakeRegenerate
) -> None:
    await flow.request(1, 10, "item-1")
    await flow.request(1, 10, "item-2")

    assert await flow.cancel(1, 10, "item-1") is False

    assert await flow.handle_comment_reply(1, 10, "comment", None) is True
    assert regenerate.calls == [("item-2", "comment")]


@pytest.mark.asyncio
async def test_has_matching_pending_is_false_with_no_pending_wait(
    flow: CommentGatedRegeneration[str],
) -> None:
    assert await flow.has_matching_pending(1, 10, "item-1") is False


@pytest.mark.asyncio
async def test_has_matching_pending_is_true_for_a_matching_second_press(
    flow: CommentGatedRegeneration[str],
) -> None:
    await flow.request(1, 10, "item-1")

    assert await flow.has_matching_pending(1, 10, "item-1") is True


@pytest.mark.asyncio
async def test_has_matching_pending_is_false_for_a_different_target(
    flow: CommentGatedRegeneration[str],
) -> None:
    await flow.request(1, 10, "item-1")

    assert await flow.has_matching_pending(1, 10, "item-2") is False
