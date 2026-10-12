from __future__ import annotations

import json

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
from content_zavod.workspaces import Workspace
from tests.tenancy import WS_A, WS_B

from .fakes import FakePendingInputs, RecordingBot, button_data, button_texts

OWNER = 1
PRIVATE = OWNER
SCREEN = 50


class FakeStore:
    """Настройки of several Пространства side by side; `values` are A's."""

    def __init__(self, values: dict[str, str] | None = None) -> None:
        self.by_workspace: dict[int, dict[str, str]] = {WS_A: dict(values or {})}

    @property
    def values(self) -> dict[str, str]:
        return self.by_workspace[WS_A]

    async def get(self, workspace_id: int, key: str) -> str | None:
        return self.by_workspace.get(workspace_id, {}).get(key)

    async def set(self, workspace_id: int, key: str, value: str) -> None:
        self.by_workspace.setdefault(workspace_id, {})[key] = value


class FakeSchedule:
    def __init__(self, config: ScheduleConfig | None = None) -> None:
        self.config = config

    async def get(self, workspace_id: int) -> ScheduleConfig | None:
        assert workspace_id == WS_A
        return self.config

    async def set(self, workspace_id: int, day_of_week: str, hour: int, minute: int) -> None:
        assert workspace_id == WS_A
        self.config = ScheduleConfig(day_of_week, hour, minute)


class FakeWorkspaces:
    def __init__(self) -> None:
        self.timezone = "Europe/Moscow"

    async def set_timezone(self, workspace_id: int, timezone: str) -> None:
        assert workspace_id == WS_A
        self.timezone = timezone


class Env:
    def __init__(self, values: dict[str, str] | None = None) -> None:
        self.store = FakeStore(values)
        self.schedule = FakeSchedule()
        self.workspace = FakeWorkspaces()
        self.bot = RecordingBot()
        self.pending = FakePendingInputs()
        self.screen = SettingsScreen(
            SettingsService(self.store),
            self.schedule,
            self.workspace,
            self.bot,
            InputPrompt(self.bot, self.pending),
        )

    @property
    def ws(self) -> Workspace:
        return Workspace(WS_A, OWNER, None, self.workspace.timezone, None, None)

    async def reply(self, text: str) -> bool:
        return await self.screen.handle_reply(self.ws, PRIVATE, OWNER, text)


@pytest.fixture
def env() -> Env:
    return Env()


# --- the screen ---


async def test_screen_shows_every_value_with_its_purpose_and_a_change_button(env: Env) -> None:
    await env.screen.send(env.ws, PRIVATE)

    ((_, text, markup, _),) = env.bot.sent
    assert text.startswith("⚙️ Настройки\nДействуют на каждую следующую генерацию.")
    assert "Ниша: не задана\n↳ из неё подбираются Темы" in text  # not the default
    assert "Стиль автора: Маркетолог-практик\n↳ от чьего лица и каким тоном" in text
    assert "Аудитория: не задана\n↳ для кого пишутся Статьи и подбираются Темы" in text
    assert "Направления" not in text  # #128
    assert "Ваш канал или сайт: не задан\n↳ ссылка на него в последнем абзаце" in text
    assert "Расписание: понедельник, 09:00" in text
    assert button_texts(markup) == [
        ["✏️ Изменить Нишу"],
        ["✏️ Изменить стиль автора"],
        ["✏️ Изменить Аудиторию"],
        ["✏️ Изменить канал или сайт"],
        ["🕘 Изменить Расписание"],
        ["◀ Назад"],
    ]
    assert button_data(markup)[-2:] == [["sc:s"], ["mn:"]]


async def test_every_setting_field_has_a_screen_line_and_a_button(env: Env) -> None:
    """A new Настройка is one SETTING_FIELDS entry - the screen picks it up by itself."""
    await env.screen.send(env.ws, PRIVATE)

    ((_, text, markup, _),) = env.bot.sent
    for setting in SETTING_FIELDS:
        assert f"{setting.label}: " in text
        assert [f"se:{setting.key}"] in button_data(markup)


async def test_show_edits_the_screen_in_place(env: Env) -> None:
    await env.screen.show(env.ws, PRIVATE, SCREEN)

    ((_, message_id, text, _),) = env.bot.edited
    assert message_id == SCREEN and text.startswith("⚙️ Настройки")
    assert env.bot.sent == []


# --- a typed Настройка ---


async def test_change_niche_asks_with_an_example_and_the_current_value(env: Env) -> None:
    await env.screen.edit(env.ws, PRIVATE, OWNER, SCREEN, "niche")

    ((_, question, markup, _),) = env.bot.sent
    assert "Например: фитнес и здоровое питание" in question
    assert "Сейчас: не задана" in question
    assert button_data(markup) == [[f"ci:{SETTING_INPUT_KIND}"]]
    assert env.pending.rows[(PRIVATE, OWNER)] == PendingInput(
        SETTING_INPUT_KIND, f"niche:{SCREEN}", 100
    )


async def test_answer_saves_closes_the_prompt_and_redraws_the_screen(env: Env) -> None:
    await env.screen.edit(env.ws, PRIVATE, OWNER, SCREEN, "niche")

    assert await env.reply("  фитнес  ") is True

    assert env.store.values["niche"] == "фитнес"
    assert env.bot.deleted == [(PRIVATE, 100)]
    ((_, message_id, text, _),) = env.bot.edited
    assert message_id == SCREEN
    assert text.startswith("✅ Ниша изменена: фитнес\n\n⚙️ Настройки")
    assert "Ниша: фитнес" in text


async def test_rejected_answer_keeps_asking_and_saves_nothing(env: Env) -> None:
    await env.screen.edit(env.ws, PRIVATE, OWNER, SCREEN, "niche")

    assert await env.reply("   ") is True

    assert "niche" not in env.store.values
    ((_, message_id, text, _),) = env.bot.edited
    assert message_id == 100
    assert text.startswith("⚠️ Ниша не может быть пустой")
    assert (PRIVATE, OWNER) in env.pending.rows
    assert await env.reply("йога") is True
    assert env.store.values["niche"] == "йога"


async def test_channel_or_site_asks_with_examples_and_dash_clears_it(env: Env) -> None:
    """#133: «Ваш канал или сайт» - any link form, the description optional."""
    await env.screen.edit(env.ws, PRIVATE, OWNER, SCREEN, "project")
    question = env.bot.sent[-1][1]
    assert "@my_channel · t.me/my_channel · example.ru · https://example.ru/blog" in question
    assert "отправьте «-»" in question

    await env.reply("ftp://x Канал")
    assert "Не получилось распознать ссылку. Подойдёт: @my_channel" in env.bot.edited[-1][2]

    await env.reply("t.me/my_channel")
    assert "Канал или сайт изменён: https://t.me/my_channel" in env.bot.edited[-1][2]
    assert "Ваш канал или сайт: https://t.me/my_channel" in env.bot.edited[-1][2]

    await env.screen.edit(env.ws, PRIVATE, OWNER, SCREEN, "project")
    await env.reply("-")
    assert "Ссылка убрана" in env.bot.edited[-1][2]


async def test_audience_asks_with_an_example_rejects_empty_and_dash_clears_it(env: Env) -> None:
    await env.screen.edit(env.ws, PRIVATE, OWNER, SCREEN, "audience")
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

    await env.screen.edit(env.ws, PRIVATE, OWNER, SCREEN, "audience")
    assert portrait in env.bot.sent[-1][1]  # the full value, line breaks kept
    await env.reply("-")
    assert env.bot.edited[-1][2].startswith("✅ Аудитория убрана")
    assert "Аудитория: не задана" in env.bot.edited[-1][2]


async def test_long_audience_is_shortened_on_the_screen_only() -> None:
    env = Env({"audience": "Владельцы малого бизнеса " * 20})

    await env.screen.send(env.ws, PRIVATE)

    ((_, text, _, _),) = env.bot.sent
    line = next(line for line in text.splitlines() if line.startswith("Аудитория: "))
    assert line.endswith("…") and len(line) < 140


async def test_set_audience_alias_saves_and_shows_the_screen(env: Env) -> None:
    await env.screen.apply_command(env.ws, PRIVATE, OWNER, "audience", "Студенты-маркетологи")
    assert env.store.values["audience"] == "Студенты-маркетологи"
    assert env.bot.sent[-1][1].startswith("✅ Аудитория изменена: Студенты-маркетологи")


async def test_screen_that_cannot_be_edited_is_sent_anew(env: Env) -> None:
    await env.screen.edit(env.ws, PRIVATE, OWNER, SCREEN, "niche")
    env.bot.fail_edits = True

    await env.reply("фитнес")

    assert env.bot.sent[-1][1].startswith("✅ Ниша изменена: фитнес")


async def test_unrelated_text_is_not_consumed(env: Env) -> None:
    assert await env.reply("привет") is False


async def test_an_answer_in_another_workspace_neither_closes_nor_writes(env: Env) -> None:
    """ADR-0015: the same chat and user ids in Workspace B don't answer A's question."""
    await env.screen.edit(env.ws, PRIVATE, OWNER, SCREEN, "niche")
    other = Workspace(WS_B, OWNER, None, "Europe/Moscow", None, None)

    assert await env.screen.handle_reply(other, PRIVATE, OWNER, "юристы") is False

    assert env.store.by_workspace.get(WS_B, {}) == {}
    assert "niche" not in env.store.values
    assert (PRIVATE, OWNER) in env.pending.rows


# --- Стиль автора (Персона): Пресеты + «Свой» ---


async def test_change_persona_opens_the_preset_chooser_in_place(env: Env) -> None:
    await env.screen.edit(env.ws, PRIVATE, OWNER, SCREEN, "persona")

    ((_, message_id, text, markup),) = env.bot.edited
    assert message_id == SCREEN
    assert "Стиль автора — от чьего лица и каким тоном пишутся Статьи." in text
    assert "Сейчас: Маркетолог-практик" in text
    assert button_texts(markup) == [
        ["✅ Маркетолог-практик"],
        ["Доказательный аналитик"],
        ["Фаундер-оператор"],
        ["Понятный наставник"],
        ["✏️ Свой стиль"],
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
    await env.screen.pick(env.ws, PRIVATE, SCREEN, "persona", 1)

    assert env.store.values["voice"] == "preset:evidence_analyst"
    ((_, _, text, _),) = env.bot.edited
    assert text.startswith("✅ Стиль автора изменён: Доказательный аналитик")


async def test_custom_persona_is_asked_with_an_example_and_saved(env: Env) -> None:
    await env.screen.ask(env.ws, PRIVATE, OWNER, "persona", screen_message_id=SCREEN)
    assert "Роль: фаундер" in env.bot.sent[0][1]

    await env.reply("Где я")
    assert "Не нашёл строку «Роль: …»" in env.bot.edited[-1][2]

    await env.reply("Название: Технооптимист\nРоль: фаундер")
    assert json.loads(env.store.values["voice"]) == {"title": "Технооптимист", "role": "фаундер"}
    assert env.bot.edited[-1][2].startswith("✅ Стиль автора изменён: Технооптимист")


# --- hidden /set_<key> aliases ---


async def test_set_alias_with_a_value_saves_and_sends_the_screen(env: Env) -> None:
    await env.screen.apply_command(env.ws, PRIVATE, OWNER, "niche", "йога")

    assert env.store.values["niche"] == "йога"
    assert env.bot.sent[0][1].startswith("✅ Ниша изменена: йога")


async def test_set_alias_without_a_value_asks_for_it(env: Env) -> None:
    await env.screen.apply_command(env.ws, PRIVATE, OWNER, "niche", " ")
    await env.reply("йога")

    assert env.store.values["niche"] == "йога"
    assert env.bot.sent[-1][1].startswith("✅ Ниша изменена: йога")  # no screen to edit


async def test_set_alias_with_a_bad_value_explains_with_the_example(env: Env) -> None:
    await env.screen.apply_command(env.ws, PRIVATE, OWNER, "persona", "просто текст")

    assert env.bot.sent[0][1].startswith("⚠️ Не нашёл строку «Роль: …»")
    assert "Например:" in env.bot.sent[0][1]


# --- Расписание ---


async def test_schedule_screen_has_russian_weekdays_and_time_button(env: Env) -> None:
    env.schedule.config = ScheduleConfig("fri", 10, 30)

    await env.screen.show_schedule(env.ws, PRIVATE, SCREEN, "m")

    ((_, _, text, markup),) = env.bot.edited
    assert "Сейчас: пятница, 10:30\nЧасовой пояс: МСК" in text
    assert button_texts(markup) == [
        ["пн", "вт", "ср", "чт"],
        ["✅ пт", "сб", "вс"],
        ["🕐 Изменить время"],
        ["🌍 Часовой пояс"],
        ["◀ Назад"],
    ]
    assert button_data(markup)[0][0] == "sd:mon:m"
    assert button_data(markup)[2:] == [["sk:m"], ["sz:m"], ["mn:"]]


async def test_schedule_back_returns_to_settings_when_opened_from_there(env: Env) -> None:
    await env.screen.show_schedule(env.ws, PRIVATE, SCREEN, "s")

    assert button_data(env.bot.edited[0][3])[-1] == ["st:"]


async def test_weekday_button_keeps_the_time(env: Env) -> None:
    env.schedule.config = ScheduleConfig("mon", 9, 15)

    await env.screen.set_day(env.ws, PRIVATE, SCREEN, "wed", "m")

    assert env.schedule.config == ScheduleConfig("wed", 9, 15)
    assert env.bot.edited[0][2].startswith("✅ День изменён: среда, 09:15")


async def test_timezone_chooser_lists_russias_zones_and_marks_the_current_one(env: Env) -> None:
    env.workspace.timezone = "Asia/Vladivostok"

    await env.screen.show_timezones(env.ws, PRIVATE, SCREEN, "s")

    ((_, message_id, text, markup),) = env.bot.edited
    assert message_id == SCREEN
    assert "Сейчас: МСК+7" in text
    assert button_texts(markup) == [
        ["МСК−1", "МСК", "МСК+1"],
        ["МСК+2", "МСК+3", "МСК+4"],
        ["МСК+5", "МСК+6", "✅ МСК+7"],
        ["МСК+8", "МСК+9"],
        ["◀ Назад"],
    ]
    assert button_data(markup)[0][0] == "sy:0:s"
    assert button_data(markup)[-1] == ["sc:s"]


async def test_picked_timezone_is_saved_and_shown_on_the_schedule(env: Env) -> None:
    await env.screen.set_timezone(env.ws, PRIVATE, SCREEN, "8", "m")

    assert env.workspace.timezone == "Asia/Vladivostok"
    _, _, text, markup = env.bot.edited[-1]
    assert text.startswith("✅ Часовой пояс изменён: МСК+7")
    assert "Часовой пояс: МСК+7" in text
    assert button_data(markup)[-1] == ["mn:"]


@pytest.mark.parametrize("index", ["11", "-1", "x", ""])
async def test_a_bad_timezone_index_changes_nothing(env: Env, index: str) -> None:
    await env.screen.set_timezone(env.ws, PRIVATE, SCREEN, index, "m")

    assert env.workspace.timezone == "Europe/Moscow"
    assert env.bot.edited[-1][2].startswith("🕘 Расписание")


async def test_time_is_asked_and_saved_keeping_the_day(env: Env) -> None:
    env.schedule.config = ScheduleConfig("thu", 9, 0)
    await env.screen.ask_time(env.ws, PRIVATE, OWNER, SCREEN, "s")
    assert "Например: 09:30" in env.bot.sent[0][1]

    await env.reply("25:00")
    assert "Не понял время «25:00»" in env.bot.edited[-1][2]
    await env.reply("18.45")

    assert env.schedule.config == ScheduleConfig("thu", 18, 45)
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
    await env.screen.set_schedule_command(env.ws, PRIVATE, "пт 10:30")

    assert env.schedule.config == ScheduleConfig("fri", 10, 30)
    assert env.bot.sent[0][1] == "Расписание изменено: пятница, 10:30"


async def test_set_schedule_alias_rejects_unknown_day(env: Env) -> None:
    await env.screen.set_schedule_command(env.ws, PRIVATE, "xx 10:30")

    assert env.bot.sent[0][1].startswith("Неизвестный день «xx»")
    assert env.schedule.config is None
