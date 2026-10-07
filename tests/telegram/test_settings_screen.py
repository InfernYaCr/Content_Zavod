from __future__ import annotations

import json
from zoneinfo import ZoneInfo

import pytest

from content_zavod.scheduling import ScheduleConfig
from content_zavod.settings import SettingsService
from content_zavod.telegram.input_prompt import InputPrompt
from content_zavod.telegram.pending_inputs import PendingInput
from content_zavod.telegram.settings_screen import (
    SETTING_FIELDS,
    SETTING_INPUT_KIND,
    SettingsScreen,
    parse_time,
)

from .fakes import FakePendingInputs, RecordingBot, button_data, button_texts

OWNER = 1
PRIVATE = OWNER
GROUP = -100777
SCREEN = 50
MOSCOW = ZoneInfo("Europe/Moscow")


class FakeStore:
    def __init__(self, values: dict[str, str] | None = None) -> None:
        self.values = dict(values or {})

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def set(self, key: str, value: str) -> None:
        self.values[key] = value


class FakeSchedule:
    def __init__(self, config: ScheduleConfig | None = None) -> None:
        self.config = config

    async def get(self) -> ScheduleConfig | None:
        return self.config

    async def set(self, day_of_week: str, hour: int, minute: int) -> None:
        self.config = ScheduleConfig(day_of_week, hour, minute)


class FakeScheduler:
    def __init__(self) -> None:
        self.rescheduled: list[tuple[str, object]] = []

    def reschedule_job(self, job_id, *, trigger) -> None:
        self.rescheduled.append((job_id, trigger))


class FakeSuggester:
    """Records what `DirectionSuggestions.request` was asked (#113)."""

    def __init__(self) -> None:
        self.requested: list[tuple[int, str, bool]] = []

    async def request(self, chat_id: int, origin: str, *, niche_changed: bool = False) -> None:
        self.requested.append((chat_id, origin, niche_changed))


class Env:
    def __init__(
        self, values: dict[str, str] | None = None, *, directions: FakeSuggester | None = None
    ) -> None:
        self.store = FakeStore(values)
        self.schedule = FakeSchedule()
        self.scheduler = FakeScheduler()
        self.bot = RecordingBot()
        self.pending = FakePendingInputs()
        self.screen = SettingsScreen(
            SettingsService(self.store),
            self.schedule,
            self.scheduler,
            self.bot,
            InputPrompt(self.bot, self.pending),
            tz=MOSCOW,
            directions=directions,
        )

    async def reply(self, text: str, chat_id: int = PRIVATE, reply_to: int | None = None) -> bool:
        return await self.screen.handle_reply(chat_id, OWNER, text, reply_to, role="owner")


@pytest.fixture
def env() -> Env:
    return Env()


# --- the screen ---


async def test_screen_shows_every_value_with_its_purpose_and_a_change_button(env: Env) -> None:
    await env.screen.send(PRIVATE)

    ((_, text, markup, _),) = env.bot.sent
    assert text.startswith("⚙️ Настройки\nДействуют на каждую следующую генерацию.")
    assert "Ниша: маркетинг\n↳ из неё подбираются Темы" in text
    assert "Персона: Маркетолог-практик\n↳ от чьего лица пишутся Статьи" in text
    assert "Аудитория: не задана\n↳ для кого пишутся Статьи и подбираются Темы" in text
    assert "Направления: crm для малого бизнеса, email маркетинг" in text
    assert "Проект: не задан\n↳" in text
    assert "Расписание: понедельник, 09:00" in text
    assert button_texts(markup) == [
        ["✏️ Изменить Нишу"],
        ["✏️ Изменить Персону"],
        ["✏️ Изменить Аудиторию"],
        ["✏️ Изменить Направления"],
        ["✏️ Изменить Проект"],
        ["🕘 Изменить Расписание"],
        ["◀ Назад"],
    ]
    assert button_data(markup)[-2:] == [["sc:s"], ["mn:"]]


async def test_every_setting_field_has_a_screen_line_and_a_button(env: Env) -> None:
    """A new Настройка is one SETTING_FIELDS entry - the screen picks it up by itself."""
    await env.screen.send(PRIVATE)

    ((_, text, markup, _),) = env.bot.sent
    for setting in SETTING_FIELDS:
        assert f"{setting.label}: " in text
        assert [f"se:{setting.key}"] in button_data(markup)


async def test_show_edits_the_screen_in_place(env: Env) -> None:
    await env.screen.show(PRIVATE, SCREEN)

    ((_, message_id, text, _),) = env.bot.edited
    assert message_id == SCREEN and text.startswith("⚙️ Настройки")
    assert env.bot.sent == []


# --- a typed Настройка ---


async def test_change_niche_asks_with_an_example_and_the_current_value(env: Env) -> None:
    await env.screen.edit(PRIVATE, OWNER, SCREEN, "niche")

    ((_, question, markup, _),) = env.bot.sent
    assert "Например: фитнес и здоровое питание" in question
    assert "Сейчас: маркетинг" in question
    assert button_data(markup) == [[f"ci:{SETTING_INPUT_KIND}"]]
    assert env.pending.rows[(PRIVATE, OWNER)] == PendingInput(
        SETTING_INPUT_KIND, f"niche:{SCREEN}", 100, None
    )


async def test_answer_saves_closes_the_prompt_and_redraws_the_screen(env: Env) -> None:
    await env.screen.edit(PRIVATE, OWNER, SCREEN, "niche")

    assert await env.reply("  фитнес  ") is True

    assert env.store.values["niche"] == "фитнес"
    assert env.bot.deleted == [(PRIVATE, 100)]
    ((_, message_id, text, _),) = env.bot.edited
    assert message_id == SCREEN
    assert text.startswith("✅ Ниша изменена: фитнес\n\n⚙️ Настройки")
    assert "Ниша: фитнес" in text


async def test_group_answer_must_reply_to_the_prompt(env: Env) -> None:
    await env.screen.edit(GROUP, OWNER, SCREEN, "niche")

    assert await env.reply("фитнес", chat_id=GROUP, reply_to=12345) is False
    assert await env.reply("фитнес", chat_id=GROUP, reply_to=101) is True
    assert env.store.values["niche"] == "фитнес"
    assert env.bot.deleted == [(GROUP, 100), (GROUP, 101)]


async def test_rejected_answer_keeps_asking_and_saves_nothing(env: Env) -> None:
    await env.screen.edit(PRIVATE, OWNER, SCREEN, "directions")

    assert await env.reply(" , , ") is True

    assert "directions" not in env.store.values
    ((_, message_id, text, _),) = env.bot.edited
    assert message_id == 100
    assert text.startswith("⚠️ Нужно хотя бы одно Направление")
    assert (PRIVATE, OWNER) in env.pending.rows
    assert await env.reply("йога, пилатес") is True
    assert env.store.values["directions"] == "йога, пилатес"


async def test_project_link_error_is_specific_and_dash_clears_it(env: Env) -> None:
    await env.screen.edit(PRIVATE, OWNER, SCREEN, "project")
    await env.reply("ftp://x Канал")
    assert "Ссылка не распознана" in env.bot.edited[-1][2]

    await env.reply("@my_channel Канал о маркетинге")
    assert "Проект изменён: https://t.me/my_channel — Канал о маркетинге" in env.bot.edited[-1][2]

    await env.screen.edit(PRIVATE, OWNER, SCREEN, "project")
    await env.reply("-")
    assert "Проект убран" in env.bot.edited[-1][2]


async def test_audience_asks_with_an_example_rejects_empty_and_dash_clears_it(env: Env) -> None:
    await env.screen.edit(PRIVATE, OWNER, SCREEN, "audience")
    ((_, question, _, _),) = env.bot.sent
    assert "Например:" in question
    assert "Сейчас: не задана" in question

    await env.reply("   ")
    assert "audience" not in env.store.values
    assert env.bot.edited[-1][2].startswith("⚠️ Аудитория не может быть пустой")

    portrait = "Владельцы кофеен и пекарен.\nБоль — нет времени разбираться в рекламе."
    await env.reply(portrait)
    screen = env.bot.edited[-1][2]
    assert screen.startswith("✅ Аудитория изменена: Владельцы кофеен и пекарен.")
    assert "Аудитория: Владельцы кофеен и пекарен. Боль — нет времени" in screen

    await env.screen.edit(PRIVATE, OWNER, SCREEN, "audience")
    assert portrait in env.bot.sent[-1][1]  # the full value, line breaks kept
    await env.reply("-")
    assert env.bot.edited[-1][2].startswith("✅ Аудитория убрана")
    assert "Аудитория: не задана" in env.bot.edited[-1][2]


async def test_long_audience_is_shortened_on_the_screen_only() -> None:
    env = Env({"audience": "Владельцы малого бизнеса " * 20})

    await env.screen.send(PRIVATE)

    ((_, text, _, _),) = env.bot.sent
    line = next(line for line in text.splitlines() if line.startswith("Аудитория: "))
    assert line.endswith("…") and len(line) < 140


async def test_set_audience_alias_saves_and_shows_the_screen(env: Env) -> None:
    await env.screen.apply_command(PRIVATE, OWNER, "audience", "Студенты-маркетологи")
    assert env.store.values["audience"] == "Студенты-маркетологи"
    assert env.bot.sent[-1][1].startswith("✅ Аудитория изменена: Студенты-маркетологи")


async def test_screen_that_cannot_be_edited_is_sent_anew(env: Env) -> None:
    await env.screen.edit(PRIVATE, OWNER, SCREEN, "niche")
    env.bot.fail_edits = True

    await env.reply("фитнес")

    assert env.bot.sent[-1][1].startswith("✅ Ниша изменена: фитнес")


async def test_answer_from_someone_no_longer_owner_is_swallowed(env: Env) -> None:
    await env.screen.edit(PRIVATE, OWNER, SCREEN, "niche")

    consumed = await env.screen.handle_reply(PRIVATE, OWNER, "x", None, role="content_manager")

    assert consumed is True
    assert "niche" not in env.store.values
    assert env.bot.deleted == [(PRIVATE, 100)]


async def test_unrelated_text_is_not_consumed(env: Env) -> None:
    assert await env.reply("привет") is False


# --- Персона: Пресеты + «Своя» ---


async def test_change_persona_opens_the_preset_chooser_in_place(env: Env) -> None:
    await env.screen.edit(PRIVATE, OWNER, SCREEN, "persona")

    ((_, message_id, text, markup),) = env.bot.edited
    assert message_id == SCREEN
    assert "Персона — от чьего лица пишутся Статьи." in text
    assert "Сейчас: Маркетолог-практик" in text
    assert button_texts(markup) == [
        ["✅ Маркетолог-практик"],
        ["Доказательный аналитик"],
        ["Фаундер-оператор"],
        ["Понятный наставник"],
        ["✏️ Своя Персона"],
        ["◀ Назад"],
    ]
    assert button_data(markup)[1:] == [
        ["sp:persona:1"],
        ["sp:persona:2"],
        ["sp:persona:3"],
        ["sa:persona"],
        ["st:"],
    ]
    assert env.bot.sent == []


async def test_picking_a_preset_saves_it_and_returns_to_the_screen(env: Env) -> None:
    await env.screen.pick(PRIVATE, SCREEN, "persona", 1)

    assert env.store.values["voice"] == "preset:evidence_analyst"
    ((_, _, text, _),) = env.bot.edited
    assert text.startswith("✅ Персона изменена: Доказательный аналитик")


async def test_custom_persona_is_asked_with_an_example_and_saved(env: Env) -> None:
    await env.screen.ask(PRIVATE, OWNER, "persona", screen_message_id=SCREEN)
    assert "Роль: фаундер" in env.bot.sent[0][1]

    await env.reply("Где я")
    assert "Не нашёл строку «Роль: …»" in env.bot.edited[-1][2]

    await env.reply("Название: Технооптимист\nРоль: фаундер")
    assert json.loads(env.store.values["voice"]) == {"title": "Технооптимист", "role": "фаундер"}
    assert env.bot.edited[-1][2].startswith("✅ Персона изменена: Технооптимист")


# --- hidden /set_<key> aliases ---


async def test_set_alias_with_a_value_saves_and_sends_the_screen(env: Env) -> None:
    await env.screen.apply_command(PRIVATE, OWNER, "niche", "йога")

    assert env.store.values["niche"] == "йога"
    assert env.bot.sent[0][1].startswith("✅ Ниша изменена: йога")


async def test_set_alias_without_a_value_asks_for_it(env: Env) -> None:
    await env.screen.apply_command(PRIVATE, OWNER, "niche", " ")
    await env.reply("йога")

    assert env.store.values["niche"] == "йога"
    assert env.bot.sent[-1][1].startswith("✅ Ниша изменена: йога")  # no screen to edit


async def test_set_alias_with_a_bad_value_explains_with_the_example(env: Env) -> None:
    await env.screen.apply_command(PRIVATE, OWNER, "persona", "просто текст")

    assert env.bot.sent[0][1].startswith("⚠️ Не нашёл строку «Роль: …»")
    assert "Например:" in env.bot.sent[0][1]


# --- Расписание ---


async def test_schedule_screen_has_russian_weekdays_and_time_button(env: Env) -> None:
    env.schedule.config = ScheduleConfig("fri", 10, 30)

    await env.screen.show_schedule(PRIVATE, SCREEN, "m")

    ((_, _, text, markup),) = env.bot.edited
    assert "Сейчас: пятница, 10:30" in text
    assert button_texts(markup) == [
        ["пн", "вт", "ср", "чт"],
        ["✅ пт", "сб", "вс"],
        ["🕐 Изменить время"],
        ["◀ Назад"],
    ]
    assert button_data(markup)[0][0] == "sd:mon:m"
    assert button_data(markup)[2:] == [["sk:m"], ["mn:"]]


async def test_schedule_back_returns_to_settings_when_opened_from_there(env: Env) -> None:
    await env.screen.show_schedule(PRIVATE, SCREEN, "s")

    assert button_data(env.bot.edited[0][3])[-1] == ["st:"]


async def test_weekday_button_keeps_the_time_and_reschedules(env: Env) -> None:
    env.schedule.config = ScheduleConfig("mon", 9, 15)

    await env.screen.set_day(PRIVATE, SCREEN, "wed", "m")

    assert env.schedule.config == ScheduleConfig("wed", 9, 15)
    ((job_id, trigger),) = env.scheduler.rescheduled
    assert job_id == "weekly_plan_trigger"
    assert "day_of_week='wed'" in str(trigger) and "hour='9'" in str(trigger)
    assert env.bot.edited[0][2].startswith("✅ День изменён: среда, 09:15")


async def test_time_is_asked_and_saved_keeping_the_day(env: Env) -> None:
    env.schedule.config = ScheduleConfig("thu", 9, 0)
    await env.screen.ask_time(PRIVATE, OWNER, SCREEN, "s")
    assert "Например: 09:30" in env.bot.sent[0][1]

    await env.reply("25:00")
    assert "Не понял время «25:00»" in env.bot.edited[-1][2]
    await env.reply("18.45")

    assert env.schedule.config == ScheduleConfig("thu", 18, 45)
    assert len(env.scheduler.rescheduled) == 1
    _, message_id, text, markup = env.bot.edited[-1]
    assert message_id == SCREEN
    assert text.startswith("✅ Время изменено: четверг, 18:45")
    assert button_data(markup)[-1] == ["st:"]


@pytest.mark.parametrize(
    ("text", "expected"),
    [("09:30", (9, 30)), ("9.30", (9, 30)), (" 9 30 ", (9, 30)), ("18", (18, 0))],
)
def test_parse_time_accepts_phone_friendly_forms(text, expected) -> None:
    assert parse_time(text) == expected


@pytest.mark.parametrize("text", ["24:00", "9:7", "утром", ""])
def test_parse_time_rejects_anything_else(text) -> None:
    assert parse_time(text) is None


async def test_set_schedule_alias_still_works(env: Env) -> None:
    await env.screen.set_schedule_command(PRIVATE, "пт 10:30")

    assert env.schedule.config == ScheduleConfig("fri", 10, 30)
    assert env.bot.sent[0][1] == "Расписание изменено: пятница, 10:30"


async def test_set_schedule_alias_rejects_unknown_day(env: Env) -> None:
    await env.screen.set_schedule_command(PRIVATE, "xx 10:30")

    assert env.bot.sent[0][1].startswith("Неизвестный день «xx»")
    assert env.scheduler.rescheduled == []


# --- Направления suggested from the Ниша (#113) ---


async def test_directions_question_offers_a_suggestion_by_niche(env: Env) -> None:
    await env.screen.edit(PRIVATE, OWNER, SCREEN, "directions")

    ((_, _, markup, _),) = env.bot.sent
    assert button_texts(markup) == [["✨ Предложить по Нише"], ["Отмена"]]
    assert button_data(markup)[0] == [f"ds:s:{SCREEN}"]


async def test_a_rejected_directions_answer_keeps_the_suggestion_button(env: Env) -> None:
    await env.screen.edit(PRIVATE, OWNER, SCREEN, "directions")

    await env.reply(" , ")

    assert button_data(env.bot.edited[-1][3])[0] == [f"ds:s:{SCREEN}"]


async def test_other_questions_have_no_suggestion_button(env: Env) -> None:
    await env.screen.edit(PRIVATE, OWNER, SCREEN, "niche")

    assert button_texts(env.bot.sent[-1][2]) == [["Отмена"]]


async def test_new_niche_with_default_directions_starts_a_suggestion_at_once() -> None:
    suggester = FakeSuggester()
    env = Env(directions=suggester)
    await env.screen.edit(PRIVATE, OWNER, SCREEN, "niche")

    await env.reply("домашняя выпечка")

    assert suggester.requested == [(PRIVATE, f"s:{SCREEN}", True)]
    assert "directions" not in env.store.values  # nothing saved until «✅ Взять»


async def test_new_niche_with_own_directions_suggests_nothing() -> None:
    suggester = FakeSuggester()
    env = Env({"directions": "торт, хлеб"}, directions=suggester)
    await env.screen.edit(PRIVATE, OWNER, SCREEN, "niche")

    await env.reply("домашняя выпечка")

    assert suggester.requested == []


async def test_set_niche_alias_also_starts_a_suggestion_for_the_new_screen() -> None:
    suggester = FakeSuggester()
    env = Env(directions=suggester)

    await env.screen.apply_command(PRIVATE, OWNER, "niche", "ремонт квартир")

    assert suggester.requested == [(PRIVATE, "s:100", True)]  # the screen it just sent


async def test_the_same_niche_sent_again_starts_no_second_suggestion() -> None:
    suggester = FakeSuggester()
    env = Env(directions=suggester)

    await env.screen.apply_command(PRIVATE, OWNER, "niche", "ремонт квартир")
    await env.screen.apply_command(PRIVATE, OWNER, "niche", "ремонт квартир")
    await env.screen.edit(PRIVATE, OWNER, SCREEN, "niche")
    await env.reply("ремонт квартир")

    assert len(suggester.requested) == 1


async def test_opening_a_suggestion_drops_the_directions_wait(env: Env) -> None:
    await env.screen.edit(PRIVATE, OWNER, SCREEN, "directions")

    await env.screen.suggestion_opened(PRIVATE, OWNER, 100, str(SCREEN))

    assert env.pending.rows == {}
    assert env.bot.deleted == []  # the question message becomes «⏳ Подбираю…»


async def test_taken_suggestion_redraws_the_screen_with_the_notice(env: Env) -> None:
    await env.screen.suggestion_taken(PRIVATE, OWNER, str(SCREEN), "Направления изменены: торт")

    ((_, message_id, text, _),) = env.bot.edited
    assert message_id == SCREEN
    assert text.startswith("✅ Направления изменены: торт\n\n⚙️ Настройки")


async def test_taken_suggestion_without_a_screen_sends_one(env: Env) -> None:
    await env.screen.suggestion_taken(PRIVATE, OWNER, "0", "Направления изменены: торт")

    assert env.bot.edited == []
    assert env.bot.sent[-1][1].startswith("✅ Направления изменены: торт")


async def test_write_own_asks_the_directions_question_again(env: Env) -> None:
    await env.screen.suggestion_declined(PRIVATE, OWNER, str(SCREEN), write_own=True)

    assert env.pending.rows[(PRIVATE, OWNER)].target_id == f"directions:{SCREEN}"


async def test_cancelled_suggestion_leaves_the_screen_alone(env: Env) -> None:
    await env.screen.suggestion_declined(PRIVATE, OWNER, str(SCREEN), write_own=False)

    assert env.bot.sent == [] and env.bot.edited == [] and env.pending.rows == {}
