"""The Владелец's first-run wizard (#96), driven through `Onboarding` with in-memory fakes."""

from __future__ import annotations

from dataclasses import replace

import pytest

from content_zavod.scheduling import ScheduleConfig
from content_zavod.settings import OnboardingState, SettingsService
from content_zavod.telegram.input_prompt import InputPrompt
from content_zavod.telegram.onboarding import (
    ONBOARDING_INPUT_KIND,
    ONBOARDING_ORDER,
    Onboarding,
    StepRef,
    onboarding_fields,
)
from content_zavod.telegram.pending_inputs import PendingInput
from content_zavod.telegram.settings_screen import SETTING_FIELDS

from .fakes import FakePendingInputs, RecordingBot, button_data, button_texts

OWNER = 1
PRIVATE = OWNER
GROUP = -100777
KIND = ONBOARDING_INPUT_KIND


class FakeStore:
    def __init__(self, values: dict[str, str] | None = None) -> None:
        self.values = dict(values or {})

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def set(self, key: str, value: str) -> None:
        self.values[key] = value

    async def set_if_changed(self, key: str, value: str) -> bool:
        changed = self.values.get(key) != value
        self.values[key] = value
        return changed


class FakeSchedule:
    async def get(self) -> ScheduleConfig | None:
        return ScheduleConfig("wed", 10, 30)

    async def set(self, day_of_week: str, hour: int, minute: int) -> None:
        pass


class FakeMenu:
    def __init__(self) -> None:
        self.shown: list[tuple[int, int, str]] = []
        self.generated: list[int] = []

    async def show(self, chat_id: int, message_id: int, role: str) -> None:
        self.shown.append((chat_id, message_id, role))

    async def generate_plan(self, chat_id: int) -> None:
        self.generated.append(chat_id)


class Env:
    def __init__(self, values: dict[str, str] | None = None, **kwargs) -> None:
        self.store = FakeStore(values)
        self.bot = RecordingBot()
        self.pending = FakePendingInputs()
        self.menu = FakeMenu()
        self.state = OnboardingState(self.store)
        self.settings = SettingsService(self.store)
        self.onboarding = Onboarding(
            self.state,
            self.settings,
            InputPrompt(self.bot, self.pending),
            self.bot,
            self.menu,
            FakeSchedule(),
            **kwargs,
        )

    @property
    def last(self) -> tuple[int, str, object, str | None]:
        return self.bot.sent[-1]

    @property
    def wait(self) -> PendingInput | None:
        return self.pending.rows.get((PRIVATE, OWNER))

    async def press(self, step_id: str) -> None:
        """Press an `onboarding_step` button on the newest message."""
        await self.onboarding.go(PRIVATE, OWNER, self.bot._next_id - 1, step_id)

    async def answer(self, text: str) -> bool:
        return await self.onboarding.handle_reply(PRIVATE, OWNER, text, None, role="owner")


@pytest.fixture
def env() -> Env:
    return Env()


def _data(markup) -> list[str]:
    return [data for row in button_data(markup) for data in row]


# --- which keys are steps ---


def test_audience_follows_niche_and_every_step_is_a_settings_field() -> None:
    keys = [setting.key for setting in onboarding_fields()]

    assert keys == ["niche", "audience", "persona", "directions", "project"]
    assert all(setting in SETTING_FIELDS for setting in onboarding_fields())


def test_keys_without_a_settings_field_are_skipped() -> None:
    """A step appears exactly when `SETTING_FIELDS` has its field - Аудитория (#100) plugged
    in this way with no change to the wizard."""
    without_audience = [setting for setting in SETTING_FIELDS if setting.key != "audience"]

    keys = [setting.key for setting in onboarding_fields(without_audience)]

    assert keys == [key for key in ONBOARDING_ORDER if key != "audience"]


def test_step_ref_round_trips() -> None:
    for ref in (StepRef("niche"), StepRef("persona", True, True), StepRef("project", True)):
        assert StepRef.decode(ref.encode()) == ref


# --- /start ---


async def test_offer_sends_the_intro_to_a_fresh_owner(env: Env) -> None:
    assert await env.onboarding.offer(PRIVATE, OWNER, "owner") is True

    ((_, text, markup, _),) = env.bot.sent
    assert "Я помогаю команде вести блог на Дзене и VC.ru" in text
    assert "5 коротких шагов — Ниша → Аудитория → Персона → Направления → Проект" in text
    assert "/menu" in text
    assert button_texts(markup) == [["▶ Начать настройку"], ["Позже — открыть меню"]]
    assert button_data(markup) == [["ob:start"], ["ox:"]]


async def test_offer_is_nothing_for_a_content_manager(env: Env) -> None:
    assert await env.onboarding.offer(PRIVATE, OWNER, "content_manager") is False
    assert env.bot.sent == []


async def test_offer_is_nothing_for_an_install_that_already_has_a_niche() -> None:
    env = Env({"niche": "фитнес"})

    assert await env.onboarding.offer(PRIVATE, OWNER, "owner") is False
    assert env.bot.sent == []


async def test_offer_in_a_group_points_to_the_private_chat() -> None:
    env = Env(bot_username="zavod_bot")

    assert await env.onboarding.offer(GROUP, OWNER, "owner") is False

    ((chat_id, text, markup, _),) = env.bot.sent
    assert chat_id == GROUP and "личном чате" in text
    assert button_data(markup) == [["https://t.me/zavod_bot?start=setup"]]


async def test_wizard_buttons_pressed_in_a_group_only_point_to_the_private_chat(
    env: Env,
) -> None:
    await env.onboarding.go(GROUP, OWNER, 5, "start")
    await env.onboarding.launch(GROUP, OWNER, 5)

    assert [text for _, text, _, _ in env.bot.sent] == [env.bot.sent[0][1]] * 2
    assert "личном чате" in env.bot.sent[0][1]
    assert env.menu.generated == [] and env.store.values == {}


# --- the steps ---


async def test_start_asks_for_the_niche_first_with_an_example_and_no_skip(env: Env) -> None:
    await env.onboarding.offer(PRIVATE, OWNER, "owner")

    await env.press("start")

    assert env.store.values["onboarding"] == "started"
    assert env.bot.deleted == [(PRIVATE, 100)]  # the intro
    _, question, markup, _ = env.last
    assert question.startswith("Шаг 1 из 5 · Ниша\n↳ из неё подбираются Темы")
    assert "Например: фитнес и здоровое питание" in question
    assert "Сейчас: маркетинг" in question
    assert button_texts(markup) == [["◀ Назад"], ["Отмена"]]
    assert button_data(markup) == [["ob:intro"], [f"ci:{KIND}"]]
    assert env.wait == PendingInput(KIND, "niche", 101, None)


async def test_answer_saves_and_moves_on_to_describe_the_reader(env: Env) -> None:
    await env.press("start")
    question_id = env.wait.prompt_message_id

    assert await env.answer("фитнес") is True

    assert (await env.settings.read()).niche == "фитнес"
    assert (PRIVATE, question_id) in env.bot.deleted  # the answered question
    _, question, markup, _ = env.last
    assert question.startswith("✅ Ниша изменена: фитнес\n\nШаг 2 из 5 · Аудитория")
    assert "Опишите вашего читателя" in question
    assert button_texts(markup) == [["◀ Назад", "Пропустить ⏭"], ["Отмена"]]
    assert button_data(markup)[0] == ["ob:niche", "ob:persona"]
    assert env.wait.target_id == "audience"


async def test_audience_answer_is_saved(env: Env) -> None:
    await env.press("audience")

    await env.answer("Владельцы кофеен, 30–45 лет, маркетингом занимаются сами")

    assert "кофеен" in (await env.settings.read()).audience
    assert env.wait.target_id == "persona"


async def test_rejected_answer_keeps_the_question_and_its_buttons(env: Env) -> None:
    await env.press("start")
    question_id = env.wait.prompt_message_id

    await env.answer("   ")

    ((_, message_id, text, markup),) = env.bot.edited
    assert message_id == question_id
    assert text.startswith("⚠️ Ниша не может быть пустой.\n\nШаг 1 из 5 · Ниша")
    assert button_data(markup) == [["ob:intro"], [f"ci:{KIND}"]]
    assert env.wait.target_id == "niche"


async def test_persona_step_offers_the_presets_as_buttons(env: Env) -> None:
    await env.press("persona")

    _, question, markup, _ = env.last
    assert question.startswith("Шаг 3 из 5 · Персона")
    assert "Выберите готовый вариант или задайте свой." in question
    data = _data(markup)
    persona = next(setting for setting in SETTING_FIELDS if setting.key == "persona")
    assert [f"op:persona:{index}" for index in range(len(persona.presets))] == data[
        : len(persona.presets)
    ]
    assert "ob:persona:c" in data and "ob:audience" in data and "ob:directions" in data
    assert "✏️ Своя Персона" in [text for row in button_texts(markup) for text in row]


async def test_picking_a_preset_saves_it_and_moves_on(env: Env) -> None:
    await env.press("persona")
    persona = next(setting for setting in SETTING_FIELDS if setting.key == "persona")

    await env.onboarding.pick(PRIVATE, OWNER, 100, "persona:1")

    assert env.store.values["voice"] == persona.presets[1][1]
    assert env.wait.target_id == "directions"
    assert env.last[1].startswith(f"✅ Персона изменена: {persona.presets[1][0]}")


async def test_own_persona_asks_the_settings_question_and_back_returns_to_presets(
    env: Env,
) -> None:
    await env.press("persona")
    await env.press("persona:c")

    _, question, markup, _ = env.last
    assert "Опишите свою Персону" in question and "Роль:" in question
    assert button_data(markup)[0] == ["ob:persona", "ob:directions"]

    await env.answer("Тон: дерзкий")  # no «Роль»

    assert "Не нашёл строку «Роль: …»" in env.bot.edited[-1][2]
    assert env.wait.target_id == "persona:c"


async def test_directions_warn_when_the_niche_is_not_marketing() -> None:
    env = Env({"niche": "фитнес"})

    await env.press("directions")

    assert "⚠️ Сейчас здесь запросы про маркетинг. Для Ниши «фитнес»" in env.last[1]


async def test_directions_have_no_warning_for_the_default_niche(env: Env) -> None:
    await env.press("directions")

    assert "⚠️" not in env.last[1]


async def test_back_from_the_first_step_shows_the_intro_again(env: Env) -> None:
    await env.press("start")

    await env.press("intro")

    assert env.wait is None
    assert env.last[1].startswith("👋 Здравствуйте!")


async def test_skipping_the_last_step_shows_the_review_card(env: Env) -> None:
    await env.press("project")

    await env.press("review")

    _, text, markup, _ = env.last
    assert text.startswith("🔎 Проверьте вводные\n\nНиша: маркетинг\nАудитория: ")
    assert "Проект: не задан" in text
    assert button_data(markup) == [
        ["og:"],
        ["ob:niche:r"],
        ["ob:audience:r"],
        ["ob:persona:r"],
        ["ob:directions:r"],
        ["ob:project:r"],
    ]
    assert button_texts(markup)[0] == ["🚀 Запустить"]
    assert ["✏️ Изменить Нишу"] in button_texts(markup)
    assert env.wait is None


async def test_change_from_the_review_returns_to_it(env: Env) -> None:
    await env.press("review")
    review_card = env.bot._next_id - 1

    await env.press("niche:r")

    assert (PRIVATE, review_card) in env.bot.deleted
    assert button_data(env.last[2]) == [["ob:review"], [f"ci:{KIND}"]]  # no skip

    await env.answer("психология")

    assert env.last[1].startswith("✅ Ниша изменена: психология\n\n🔎 Проверьте вводные")


async def test_a_step_unknown_to_this_build_falls_back_to_the_review(env: Env) -> None:
    await env.press("tone")

    assert env.last[1].startswith("🔎 Проверьте вводные")


# --- the end ---


async def test_launch_finishes_and_starts_the_first_plan_once(env: Env) -> None:
    await env.press("start")

    await env.onboarding.launch(PRIVATE, OWNER, 77)
    await env.onboarding.launch(PRIVATE, OWNER, 77)  # double tap

    assert env.menu.generated == [PRIVATE]
    (_, _, launched, markup), (_, _, again, _) = env.bot.edited
    assert launched.startswith("🚀 Запускаю!")
    assert "2–5 минут" in launched and "чат команды" in launched
    assert "среда, 10:30" in launched
    assert button_data(markup) == [["mn:"]]
    assert again.startswith("✅ Вводные сохранены. Первый План уже запущен")
    assert await env.state.needed() is False


async def test_later_ends_the_wizard_and_shows_the_menu(env: Env) -> None:
    await env.onboarding.later(PRIVATE, OWNER, 100)

    assert env.menu.shown == [(PRIVATE, 100, "owner")]
    assert await env.onboarding.offer(PRIVATE, OWNER, "owner") is False


async def test_an_unfinished_wizard_is_offered_again_on_start(env: Env) -> None:
    await env.press("start")
    await env.answer("фитнес")

    assert await env.onboarding.offer(PRIVATE, OWNER, "owner") is True


# --- interruptions ---


async def test_a_command_interrupts_the_open_question(env: Env) -> None:
    await env.press("start")
    question_id = env.wait.prompt_message_id

    await env.onboarding.interrupt(PRIVATE, OWNER)

    assert env.wait is None
    assert (PRIVATE, question_id) in env.bot.deleted
    assert await env.answer("просто текст") is False
    assert (await env.settings.read()).niche == "маркетинг"


async def test_an_answer_from_someone_no_longer_owner_saves_nothing(env: Env) -> None:
    await env.press("start")

    consumed = await env.onboarding.handle_reply(
        PRIVATE, OWNER, "фитнес", None, role="content_manager"
    )

    assert consumed is True and env.wait is None
    assert "niche" not in env.store.values


async def test_other_kinds_of_waits_are_not_taken(env: Env) -> None:
    await env.pending.put(PRIVATE, OWNER, PendingInput("setting_input", "niche:5", 9))

    assert await env.answer("фитнес") is False


async def test_without_audience_the_wizard_has_four_steps() -> None:
    fields = [replace(setting) for setting in SETTING_FIELDS if setting.key != "audience"]
    env = Env(fields=onboarding_fields(fields))

    await env.press("start")
    await env.answer("фитнес")

    assert env.last[1].startswith("✅ Ниша изменена: фитнес\n\nШаг 2 из 4 · Персона")
