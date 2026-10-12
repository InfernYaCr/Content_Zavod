from __future__ import annotations

from aiogram.types import InlineKeyboardButton

from content_zavod.telegram.input_prompt import InputPrompt
from content_zavod.telegram.pending_inputs import PendingInput
from tests.tenancy import WS_A, WS_B

from .fakes import FakePendingInputs, RecordingBot, button_data

USER = 5
PRIVATE = USER


def make() -> tuple[RecordingBot, FakePendingInputs, InputPrompt]:
    bot, pending = RecordingBot(), FakePendingInputs()
    return bot, pending, InputPrompt(bot, pending)


async def test_ask_is_one_message_with_cancel() -> None:
    bot, pending, prompts = make()

    await prompts.ask(WS_A, PRIVATE, USER, "k", "t", "Вопрос?")

    ((chat_id, text, markup, _),) = bot.sent
    assert chat_id == PRIVATE
    assert text.startswith("Вопрос?\n\n")
    assert "следующим сообщением" in text
    assert button_data(markup) == [["ci:k"]]
    assert pending.rows[(PRIVATE, USER)] == PendingInput("k", "t", 100)


async def test_new_ask_removes_the_replaced_waits_prompt() -> None:
    bot, pending, prompts = make()
    await pending.put(WS_A, PRIVATE, USER, PendingInput("plan_item_comment", "i", 1))

    await prompts.ask(WS_A, PRIVATE, USER, "k", "t", "Вопрос?")

    assert bot.deleted == [(PRIVATE, 1)]


async def test_take_matches_only_its_own_kind() -> None:
    _bot, _pending, prompts = make()
    await prompts.ask(WS_A, PRIVATE, USER, "k", "t", "Вопрос?")

    assert await prompts.take(WS_A, PRIVATE, USER, "other") is None
    assert await prompts.take(WS_A, PRIVATE, USER, "k") == PendingInput("k", "t", 100)


async def test_ask_again_edits_the_question_and_keeps_waiting() -> None:
    bot, pending, prompts = make()
    await prompts.ask(WS_A, PRIVATE, USER, "k", "t", "Вопрос?")
    taken = await prompts.take(WS_A, PRIVATE, USER, "k")
    assert taken is not None

    await prompts.ask_again(WS_A, PRIVATE, USER, taken, "Плохо.", "Вопрос?")

    ((_, message_id, text, markup),) = bot.edited
    assert message_id == 100
    assert text.startswith("⚠️ Плохо.\n\nВопрос?")
    assert button_data(markup) == [["ci:k"]]
    assert pending.rows[(PRIVATE, USER)] == taken


async def test_cancel_drops_the_wait_and_its_message() -> None:
    bot, pending, prompts = make()
    await prompts.ask(WS_A, PRIVATE, USER, "k", "t", "Вопрос?")

    assert await prompts.cancel(WS_A, PRIVATE, USER, "k") is True
    assert bot.deleted == [(PRIVATE, 100)]
    assert pending.rows == {}
    assert await prompts.cancel(WS_A, PRIVATE, USER, "k") is False


async def test_a_stale_cancel_keeps_a_newer_question() -> None:
    _bot, pending, prompts = make()
    await prompts.ask(WS_A, PRIVATE, USER, "k", "t", "Вопрос?")  # message 100

    assert await prompts.cancel(WS_A, PRIVATE, USER, "k", message_id=55) is False
    assert pending.rows[(PRIVATE, USER)] == PendingInput("k", "t", 100)


async def test_is_waiting_sees_a_live_wait_of_any_kind() -> None:
    _bot, _pending, prompts = make()
    assert await prompts.is_waiting(WS_A, PRIVATE, USER) is False

    await prompts.ask(WS_A, PRIVATE, USER, "some_other_flow", "t", "Вопрос?")

    assert await prompts.is_waiting(WS_A, PRIVATE, USER) is True


async def test_a_wait_is_invisible_to_another_workspace() -> None:
    """ADR-0015: the same chat and user ids in Workspace B neither see nor take A's wait."""
    _bot, pending, prompts = make()
    await prompts.ask(WS_A, PRIVATE, USER, "k", "t", "Вопрос?")

    assert await prompts.is_waiting(WS_B, PRIVATE, USER) is False
    assert await prompts.take(WS_B, PRIVATE, USER, "k") is None
    assert await prompts.cancel(WS_B, PRIVATE, USER, "k") is False
    assert pending.rows[(PRIVATE, USER)] == PendingInput("k", "t", 100)


async def test_extra_button_rows_go_above_cancel_and_survive_a_rejected_answer() -> None:
    """The onboarding wizard (#96) asks through the same prompt, with «◀ Назад» and
    «Пропустить» on the question."""
    bot, _pending, prompts = make()
    extra = [[InlineKeyboardButton(text="◀ Назад", callback_data="ob:intro")]]
    await prompts.ask(WS_A, PRIVATE, USER, "k", "t", "Вопрос?", buttons=extra)
    taken = await prompts.take(WS_A, PRIVATE, USER, "k")
    assert taken is not None

    await prompts.ask_again(WS_A, PRIVATE, USER, taken, "Плохо.", "Вопрос?", buttons=extra)

    ((_, _, asked, _),) = bot.sent
    ((_, _, _, again),) = bot.edited
    assert button_data(asked) == button_data(again) == [["ob:intro"], ["ci:k"]]
