from __future__ import annotations

from aiogram.types import ForceReply

from content_zavod.telegram.input_prompt import InputPrompt
from content_zavod.telegram.pending_inputs import PendingInput

from .fakes import FakePendingInputs, RecordingBot, button_data

USER = 5
PRIVATE = USER
GROUP = -100123


def make() -> tuple[RecordingBot, FakePendingInputs, InputPrompt]:
    bot, pending = RecordingBot(), FakePendingInputs()
    return bot, pending, InputPrompt(bot, pending)


async def test_private_ask_is_one_message_with_cancel() -> None:
    bot, pending, prompts = make()

    await prompts.ask(PRIVATE, USER, "k", "t", "Вопрос?", placeholder="p")

    ((chat_id, text, markup, _),) = bot.sent
    assert chat_id == PRIVATE
    assert text.startswith("Вопрос?\n\n")
    assert "следующим сообщением" in text
    assert button_data(markup) == [["ci:k"]]
    assert pending.rows[(PRIVATE, USER)] == PendingInput("k", "t", 100, None)


async def test_group_ask_adds_a_selective_force_reply_mention() -> None:
    bot, pending, prompts = make()

    await prompts.ask(GROUP, USER, "k", "t", "Вопрос?", placeholder="x" * 100)

    (_, _, _, _), (_, mention, force_reply, parse_mode) = bot.sent
    assert f"tg://user?id={USER}" in mention and parse_mode == "HTML"
    assert isinstance(force_reply, ForceReply) and force_reply.selective
    assert len(force_reply.input_field_placeholder) == 64
    assert pending.rows[(GROUP, USER)] == PendingInput("k", "t", 100, 101)


async def test_new_ask_removes_the_replaced_waits_prompt() -> None:
    bot, pending, prompts = make()
    await pending.put(GROUP, USER, PendingInput("plan_item_comment", "i", 1, 2))

    await prompts.ask(GROUP, USER, "k", "t", "Вопрос?", placeholder="p")

    assert bot.deleted == [(GROUP, 1), (GROUP, 2)]


async def test_take_matches_only_a_reply_to_the_prompt_in_a_group() -> None:
    _bot, _pending, prompts = make()
    await prompts.ask(GROUP, USER, "k", "t", "Вопрос?", placeholder="p")

    assert await prompts.take(GROUP, USER, "k", 999) is None
    assert await prompts.take(GROUP, USER, "other", 101) is None
    assert await prompts.take(GROUP, USER, "k", 101) == PendingInput("k", "t", 100, 101)


async def test_ask_again_edits_the_question_and_keeps_waiting() -> None:
    bot, pending, prompts = make()
    await prompts.ask(PRIVATE, USER, "k", "t", "Вопрос?", placeholder="p")
    taken = await prompts.take(PRIVATE, USER, "k", None)

    await prompts.ask_again(PRIVATE, USER, taken, "Плохо.", "Вопрос?")

    ((_, message_id, text, markup),) = bot.edited
    assert message_id == 100
    assert text.startswith("⚠️ Плохо.\n\nВопрос?")
    assert button_data(markup) == [["ci:k"]]
    assert pending.rows[(PRIVATE, USER)] == taken


async def test_cancel_drops_the_wait_and_its_messages() -> None:
    bot, pending, prompts = make()
    await prompts.ask(GROUP, USER, "k", "t", "Вопрос?", placeholder="p")

    assert await prompts.cancel(GROUP, USER, "k") is True
    assert bot.deleted == [(GROUP, 100), (GROUP, 101)]
    assert pending.rows == {}
    assert await prompts.cancel(GROUP, USER, "k") is False


async def test_is_waiting_sees_a_live_wait_of_any_kind() -> None:
    _bot, _pending, prompts = make()
    assert await prompts.is_waiting(PRIVATE, USER) is False

    await prompts.ask(PRIVATE, USER, "some_other_flow", "t", "Вопрос?", placeholder="p")

    assert await prompts.is_waiting(PRIVATE, USER) is True


async def test_extra_button_rows_go_above_cancel_and_survive_a_rejected_answer() -> None:
    """The onboarding wizard (#96) asks through the same prompt, with «◀ Назад» and
    «Пропустить» on the question."""
    from aiogram.types import InlineKeyboardButton

    bot, _pending, prompts = make()
    extra = [[InlineKeyboardButton(text="◀ Назад", callback_data="ob:intro")]]
    await prompts.ask(PRIVATE, USER, "k", "t", "Вопрос?", placeholder="p", buttons=extra)
    taken = await prompts.take(PRIVATE, USER, "k", None)

    await prompts.ask_again(PRIVATE, USER, taken, "Плохо.", "Вопрос?", buttons=extra)

    ((_, _, asked, _),) = bot.sent
    ((_, _, _, again),) = bot.edited
    assert button_data(asked) == button_data(again) == [["ob:intro"], ["ci:k"]]
