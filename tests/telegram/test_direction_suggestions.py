"""Направления suggested from the Ниша (#113): the bot half, driven through
`DirectionSuggestions` with an in-memory Job store and a recording origin."""

from __future__ import annotations

from typing import Any

import pytest

from content_zavod.settings import SettingsService
from content_zavod.telegram.direction_suggestions import (
    SUGGEST_DIRECTIONS_JOB,
    DirectionSuggestions,
)
from content_zavod.telegram.gateway import MessageGone

from .fakes import FakeJobs, RecordingBot, button_data, button_texts

OWNER = 1
CHAT = OWNER
ORIGIN = "s:50"


class FakeStore:
    def __init__(self, values: dict[str, str] | None = None) -> None:
        self.values = dict(values or {})

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def set(self, key: str, value: str) -> None:
        self.values[key] = value


class RecordingOrigin:
    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []

    async def suggestion_opened(self, chat_id, user_id, message_id, place) -> None:
        self.calls.append(("opened", message_id, place))

    async def suggestion_taken(self, chat_id, user_id, place, notice) -> None:
        self.calls.append(("taken", place, notice))

    async def suggestion_declined(self, chat_id, user_id, place, *, write_own) -> None:
        self.calls.append(("declined", place, write_own))


class GoneBot(RecordingBot):
    gone = False

    async def edit_message_text(self, chat_id, message_id, text, reply_markup=None) -> None:
        if self.gone:
            raise MessageGone(chat_id, message_id)
        await super().edit_message_text(chat_id, message_id, text, reply_markup)


OUTPUT = {
    "niche": "домашняя выпечка",
    "queries": [
        {"query": "торт на заказ", "frequency": 12400},
        {"query": "закваска для хлеба", "frequency": None},
    ],
    "dropped": ["хлеб бабушкин секрет"],
    "wordstat": "checked",
}


class Env:
    def __init__(self, bot: RecordingBot | None = None) -> None:
        self.store = FakeStore({"niche": "домашняя выпечка"})
        self.bot = bot or RecordingBot()
        self.jobs = FakeJobs()
        self.origin = RecordingOrigin()
        self.suggestions = DirectionSuggestions(self.jobs, SettingsService(self.store), self.bot)
        self.suggestions.attach("s", self.origin)

    async def started(self) -> int:
        """Press «✨» on question 100: it becomes the «⏳» message, its Job 1."""
        await self.suggestions.start(CHAT, OWNER, 100, ORIGIN)
        return 1


@pytest.fixture
def env() -> Env:
    return Env()


async def test_start_turns_the_question_into_working_and_enqueues_a_job(env: Env) -> None:
    await env.suggestions.start(CHAT, OWNER, 7, ORIGIN)

    assert env.origin.calls == [("opened", 7, "50")]
    assert env.bot.sent == []  # in place: no new message
    ((chat_id, message_id, text, markup),) = env.bot.edited
    assert (chat_id, message_id) == (CHAT, 7)
    assert text.startswith("⏳ Подбираю Направления для Ниши «домашняя выпечка»")
    assert button_data(markup) == [[f"dc:{ORIGIN}"]]
    ((job),) = env.jobs.jobs.values()
    assert job.job_type == SUGGEST_DIRECTIONS_JOB
    assert job.payload == {"chat_id": CHAT, "message_id": 7, "origin": ORIGIN, "exclude": []}
    assert "directions" not in env.store.values


async def test_a_double_tapped_suggest_starts_one_job(env: Env) -> None:
    await env.suggestions.start(CHAT, OWNER, 7, ORIGIN)
    await env.suggestions.start(CHAT, OWNER, 7, ORIGIN)

    assert len(env.jobs.jobs) == 1 and env.bot.sent == []


async def test_after_a_niche_change_it_says_why_it_started(env: Env) -> None:
    await env.suggestions.request(CHAT, ORIGIN, niche_changed=True)

    assert env.bot.sent[0][1].startswith("Ниша сменилась, а Направления остались стандартными")


async def test_result_turns_the_working_message_into_the_list(env: Env) -> None:
    job_id = await env.started()

    await env.suggestions.deliver(env.jobs.finish(job_id, OUTPUT))

    chat_id, message_id, text, markup = env.bot.edited[-1]
    assert (chat_id, message_id) == (CHAT, 100)
    assert text.startswith("✨ Направления для Ниши «домашняя выпечка»")
    assert "• торт на заказ — ищут 12 400 раз в месяц" in text
    assert "• закваска для хлеба\n" in text
    assert "Убрал — их почти не ищут в Яндексе: хлеб бабушкин секрет." in text
    assert "Пока не нажмёте, ничего не меняется" in text
    assert button_texts(markup) == [
        ["✅ Взять", "🔄 Ещё варианты"],
        ["✏️ Написать свои", "Отмена"],
    ]
    assert button_data(markup) == [["dt:1", "dm:1"], [f"do:{ORIGIN}", f"dc:{ORIGIN}"]]
    assert "directions" not in env.store.values  # nothing saved without «Взять»


async def test_unchecked_list_says_wordstat_did_not_answer(env: Env) -> None:
    job_id = await env.started()
    output = {**OUTPUT, "wordstat": "unavailable", "dropped": []}

    await env.suggestions.deliver(env.jobs.finish(job_id, output))

    assert "⚠️ Wordstat сейчас не ответил" in env.bot.edited[-1][2]


async def test_nothing_with_demand_offers_more_but_no_take(env: Env) -> None:
    job_id = await env.started()
    output = {**OUTPUT, "queries": []}

    await env.suggestions.deliver(env.jobs.finish(job_id, output))

    _, _, text, markup = env.bot.edited[-1]
    assert text.startswith("😕 Для Ниши «домашняя выпечка» не нашлось запросов")
    assert button_texts(markup) == [["🔄 Ещё варианты"], ["✏️ Написать свои", "Отмена"]]


async def test_a_failed_job_says_so_and_offers_a_retry(env: Env) -> None:
    job_id = await env.started()

    await env.suggestions.deliver(env.jobs.finish(job_id, None))

    _, message_id, text, markup = env.bot.edited[-1]
    assert message_id == 100 and text.startswith("😕 Не получилось подобрать Направления")
    assert button_data(markup) == [["dm:1"], [f"do:{ORIGIN}", f"dc:{ORIGIN}"]]


async def test_a_result_for_a_message_that_is_gone_is_dropped() -> None:
    env = Env(GoneBot())
    job_id = await env.started()
    env.bot.gone = True

    await env.suggestions.deliver(env.jobs.finish(job_id, OUTPUT))  # no raise, nothing sent

    assert env.bot.sent == []


async def test_a_question_deleted_under_the_tap_starts_no_job() -> None:
    bot = GoneBot()
    bot.gone = True
    env = Env(bot)

    await env.suggestions.start(CHAT, OWNER, 7, ORIGIN)

    assert env.jobs.jobs == {} and env.bot.sent == []


async def test_take_saves_that_list_and_returns_to_the_origin(env: Env) -> None:
    job_id = await env.started()
    env.jobs.finish(job_id, OUTPUT)

    assert await env.suggestions.take(CHAT, OWNER, 100, str(job_id)) is True

    assert env.store.values["directions"] == "торт на заказ, закваска для хлеба"
    assert (CHAT, 100) in env.bot.deleted
    assert env.origin.calls[-1] == (
        "taken",
        "50",
        "Направления изменены: торт на заказ, закваска для хлеба",
    )


@pytest.mark.parametrize("job_text", ["999", "abc", ""])
async def test_take_of_an_unknown_job_is_stale_and_saves_nothing(env: Env, job_text: str) -> None:
    assert await env.suggestions.take(CHAT, OWNER, 100, job_text) is False
    assert "directions" not in env.store.values


async def test_take_before_the_job_is_done_saves_nothing(env: Env) -> None:
    job_id = await env.started()

    assert await env.suggestions.take(CHAT, OWNER, 100, str(job_id)) is False
    assert "directions" not in env.store.values


async def test_take_after_the_niche_changed_is_stale(env: Env) -> None:
    job_id = await env.started()
    env.jobs.finish(job_id, OUTPUT)
    env.store.values["niche"] = "ремонт квартир"

    assert await env.suggestions.take(CHAT, OWNER, 100, str(job_id)) is False
    assert "directions" not in env.store.values


async def test_take_from_another_chat_saves_nothing(env: Env) -> None:
    job_id = await env.started()
    env.jobs.finish(job_id, OUTPUT)

    assert await env.suggestions.take(-100777, OWNER, 100, str(job_id)) is False
    assert "directions" not in env.store.values


async def test_take_of_another_job_type_saves_nothing(env: Env) -> None:
    other = await env.jobs.enqueue("generate_plan", {"chat_id": CHAT}, "plan")
    env.jobs.finish(other, OUTPUT)

    assert await env.suggestions.take(CHAT, OWNER, 100, str(other)) is False


async def test_more_reworks_the_same_message_avoiding_what_was_shown(env: Env) -> None:
    job_id = await env.started()
    env.jobs.finish(job_id, OUTPUT)

    assert await env.suggestions.more(CHAT, OWNER, 100, str(job_id)) is True
    assert await env.suggestions.more(CHAT, OWNER, 100, str(job_id)) is True  # double tap

    assert len(env.jobs.jobs) == 2  # one new round, not two
    second = env.jobs.jobs[2]
    assert second.payload == {
        "chat_id": CHAT,
        "message_id": 100,
        "origin": ORIGIN,
        "exclude": ["торт на заказ", "закваска для хлеба", "хлеб бабушкин секрет"],
    }
    _, message_id, text, markup = env.bot.edited[-1]
    assert message_id == 100 and text.startswith("⏳ Подбираю")
    assert button_data(markup) == [[f"dc:{ORIGIN}"]]
    assert env.bot.sent == []  # no new message


async def test_more_after_more_keeps_excluding_earlier_rounds(env: Env) -> None:
    job_id = await env.started()
    env.jobs.finish(job_id, OUTPUT)
    await env.suggestions.more(CHAT, OWNER, 100, str(job_id))
    env.jobs.finish(2, {**OUTPUT, "queries": [{"query": "эклеры", "frequency": 5}], "dropped": []})

    await env.suggestions.more(CHAT, OWNER, 100, "2")

    assert env.jobs.jobs[3].payload["exclude"][-1] == "эклеры"
    assert "торт на заказ" in env.jobs.jobs[3].payload["exclude"]


async def test_retry_after_a_failure_starts_another_round(env: Env) -> None:
    job_id = await env.started()
    env.jobs.finish(job_id, None)

    assert await env.suggestions.more(CHAT, OWNER, 100, str(job_id)) is True
    assert env.jobs.jobs[2].payload["exclude"] == []


async def test_more_of_an_unknown_job_is_stale(env: Env) -> None:
    assert await env.suggestions.more(CHAT, OWNER, 100, "42") is False
    assert env.jobs.jobs == {}


@pytest.mark.parametrize(("method", "write_own"), [("own", True), ("cancel", False)])
async def test_own_and_cancel_delete_the_list_and_go_back(
    env: Env, method: str, write_own: bool
) -> None:
    await getattr(env.suggestions, method)(CHAT, OWNER, 100, ORIGIN)

    assert env.bot.deleted == [(CHAT, 100)]
    assert env.origin.calls == [("declined", "50", write_own)]
    assert env.store.values == {"niche": "домашняя выпечка"}


async def test_unknown_origin_only_deletes_the_message(env: Env) -> None:
    await env.suggestions.cancel(CHAT, OWNER, 100, "x:1")

    assert env.bot.deleted == [(CHAT, 100)] and env.origin.calls == []
