import pytest

from content_zavod.telegram import CommentGatedRegeneration


class FakeRegenerate:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str | None]] = []

    async def __call__(self, id_: str, comment: str | None) -> None:
        self.calls.append((id_, comment))


class FakePrompt:
    def __init__(self) -> None:
        self.prompted: list[tuple[int, str]] = []

    async def prompt_for_comment(self, chat_id: int, id_: str) -> None:
        self.prompted.append((chat_id, id_))


@pytest.fixture
def regenerate() -> FakeRegenerate:
    return FakeRegenerate()


@pytest.fixture
def prompt() -> FakePrompt:
    return FakePrompt()


@pytest.fixture
def flow(regenerate: FakeRegenerate, prompt: FakePrompt) -> CommentGatedRegeneration[str]:
    return CommentGatedRegeneration(regenerate, prompt)


@pytest.mark.asyncio
async def test_first_press_prompts_for_comment_without_regenerating(
    flow: CommentGatedRegeneration[str], regenerate: FakeRegenerate, prompt: FakePrompt
) -> None:
    await flow.request(1, 10, "item-1")

    assert prompt.prompted == [(1, "item-1")]
    assert regenerate.calls == []


@pytest.mark.asyncio
async def test_comment_reply_resolves_pending_regenerate(
    flow: CommentGatedRegeneration[str], regenerate: FakeRegenerate
) -> None:
    await flow.request(1, 10, "item-1")

    consumed = await flow.handle_comment_reply(1, 10, "please make it shorter")

    assert consumed is True
    assert regenerate.calls == [("item-1", "please make it shorter")]


@pytest.mark.asyncio
async def test_comment_reply_without_pending_wait_is_ignored(flow: CommentGatedRegeneration[str]) -> None:
    consumed = await flow.handle_comment_reply(1, 10, "stray text")

    assert consumed is False


@pytest.mark.asyncio
async def test_skip_button_repeats_same_target_and_regenerates_without_comment(
    flow: CommentGatedRegeneration[str], regenerate: FakeRegenerate, prompt: FakePrompt
) -> None:
    await flow.request(1, 10, "item-1")
    await flow.request(1, 10, "item-1")

    assert regenerate.calls == [("item-1", None)]
    assert prompt.prompted == [(1, "item-1")]  # only prompted once


@pytest.mark.asyncio
async def test_new_press_on_different_target_silently_cancels_previous_wait(
    flow: CommentGatedRegeneration[str], regenerate: FakeRegenerate, prompt: FakePrompt
) -> None:
    await flow.request(1, 10, "item-1")
    await flow.request(1, 10, "item-2")

    assert prompt.prompted == [(1, "item-1"), (1, "item-2")]
    assert regenerate.calls == []

    consumed = await flow.handle_comment_reply(1, 10, "comment for item-2")
    assert consumed is True
    assert regenerate.calls == [("item-2", "comment for item-2")]


@pytest.mark.asyncio
async def test_pending_wait_is_scoped_per_chat_and_user(
    flow: CommentGatedRegeneration[str], regenerate: FakeRegenerate
) -> None:
    await flow.request(1, 10, "item-1")

    consumed = await flow.handle_comment_reply(1, 99, "wrong user")
    assert consumed is False

    consumed = await flow.handle_comment_reply(2, 10, "wrong chat")
    assert consumed is False

    consumed = await flow.handle_comment_reply(1, 10, "right one")
    assert consumed is True
    assert regenerate.calls == [("item-1", "right one")]


@pytest.mark.asyncio
async def test_cancel_clears_pending_wait_for_same_user(
    flow: CommentGatedRegeneration[str], regenerate: FakeRegenerate
) -> None:
    await flow.request(1, 10, "item-1")

    flow.cancel(1, 10)

    consumed = await flow.handle_comment_reply(1, 10, "too late")
    assert consumed is False
    assert regenerate.calls == []


@pytest.mark.asyncio
async def test_cancel_without_pending_wait_is_a_no_op(flow: CommentGatedRegeneration[str]) -> None:
    flow.cancel(1, 10)  # must not raise
