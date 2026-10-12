"""The first-run wizard of a Пространство (#96), driven through `Onboarding` with in-memory
fakes."""

from __future__ import annotations

from dataclasses import replace

import pytest

from content_zavod.scheduling import ScheduleConfig
from content_zavod.settings import OnboardingState, SettingsService
from content_zavod.telegram.asset_photos import AssetPhotos
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
from content_zavod.workspaces import Workspace
from tests.tenancy import WS_A, WS_B

from .fakes import FakePendingInputs, RecordingBot, button_data, button_texts

OWNER = 1
PRIVATE = OWNER
KIND = ONBOARDING_INPUT_KIND
WORKSPACE = Workspace(WS_A, OWNER, None, "Europe/Moscow", None, None)


class FakeStore:
    """Настройки and the onboarding flag of several Пространства; `values` are A's."""

    def __init__(self, values: dict[str, str] | None = None) -> None:
        self.by_workspace: dict[int, dict[str, str]] = {WS_A: dict(values or {})}

    @property
    def values(self) -> dict[str, str]:
        return self.by_workspace[WS_A]

    def _values(self, workspace_id: int) -> dict[str, str]:
        return self.by_workspace.setdefault(workspace_id, {})

    async def get(self, workspace_id: int, key: str) -> str | None:
        return self._values(workspace_id).get(key)

    async def set(self, workspace_id: int, key: str, value: str) -> None:
        self._values(workspace_id)[key] = value

    async def set_if_changed(self, workspace_id: int, key: str, value: str) -> bool:
        changed = self._values(workspace_id).get(key) != value
        self._values(workspace_id)[key] = value
        return changed


class FakeSchedule:
    async def get(self, workspace_id: int) -> ScheduleConfig | None:
        return ScheduleConfig("wed", 10, 30)

    async def set(self, workspace_id: int, day_of_week: str, hour: int, minute: int) -> None:
        pass


class FakeMenu:
    def __init__(self) -> None:
        self.shown: list[tuple[int, int]] = []
        self.generated: list[tuple[int, int]] = []
        self.announced: list[bool] = []

    async def show(self, chat_id: int, message_id: int) -> None:
        self.shown.append((chat_id, message_id))

    async def generate_plan(
        self, workspace: Workspace, chat_id: int, *, announce: bool = True
    ) -> None:
        self.generated.append((workspace.id, chat_id))
        self.announced.append(announce)


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
        await self.onboarding.go(WORKSPACE, PRIVATE, OWNER, self.bot._next_id - 1, step_id)

    async def answer(self, text: str) -> bool:
        return await self.onboarding.handle_reply(WORKSPACE, PRIVATE, OWNER, text)


@pytest.fixture
def env() -> Env:
    return Env()


def _data(markup) -> list[str]:
    return [data for row in button_data(markup) for data in row]


# --- which keys are steps ---


def test_audience_follows_niche_and_every_step_is_a_settings_field() -> None:
    keys = [setting.key for setting in onboarding_fields()]

    assert keys == ["niche", "audience", "persona", "project"]  # no Направления (#128)
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
    assert await env.onboarding.offer(WORKSPACE, PRIVATE) is True

    ((_, text, markup, _),) = env.bot.sent
    assert "Я помогаю вести блог на Дзене и VC.ru" in text
    assert "4 коротких шага — Ниша → Аудитория → Стиль автора → Ваш канал или сайт" in text
    assert "/menu" in text
    assert button_texts(markup) == [
        ["▶ Начать настройку"],
        ["Позже — открыть меню"],
        ["📖 Как пользоваться"],
    ]
    assert button_data(markup) == [["ob:start"], ["ox:"], ["gd:"]]


async def test_offer_is_nothing_for_an_install_that_already_has_a_niche() -> None:
    env = Env({"niche": "фитнес"})

    assert await env.onboarding.offer(WORKSPACE, PRIVATE) is False
    assert env.bot.sent == []


# --- the steps ---


async def test_start_asks_for_the_niche_first_with_an_example_and_no_skip(env: Env) -> None:
    await env.onboarding.offer(WORKSPACE, PRIVATE)

    await env.press("start")

    assert env.store.values["onboarding"] == "started"
    assert env.bot.deleted == [(PRIVATE, 100)]  # the intro
    _, question, markup, _ = env.last
    assert question.startswith("Шаг 1 из 4 · Ниша\n↳ из неё подбираются Темы")
    assert "Какая у вас Ниша" in question
    assert "Например: фитнес и здоровое питание" in question
    assert "новую" not in question  # the Экран Настроек's «Напишите новую Нишу»
    assert "Сейчас: маркетинг" in question
    assert button_texts(markup) == [["◀ Назад"], ["Отмена"]]
    assert button_data(markup) == [["ob:intro"], [f"ci:{KIND}"]]
    assert env.wait == PendingInput(KIND, "niche", 101)


async def test_answer_saves_and_moves_on_to_describe_the_reader(env: Env) -> None:
    await env.press("start")
    question_id = env.wait.prompt_message_id

    assert await env.answer("фитнес") is True

    assert (await env.settings.read(WS_A)).niche == "фитнес"
    assert (PRIVATE, question_id) in env.bot.deleted  # the answered question
    _, question, markup, _ = env.last
    assert question.startswith("✅ Ниша изменена: фитнес\n\nШаг 2 из 4 · Аудитория")
    assert "Кто ваш читатель? Опишите его своими словами" in question
    assert "Например: владельцы небольших кофеен" in question
    assert "Чтобы убрать Аудиторию" not in question  # nothing to remove on a first run
    assert button_texts(markup) == [["◀ Назад", "Пропустить ⏭"], ["Отмена"]]
    assert button_data(markup)[0] == ["ob:niche", "ob:persona"]
    assert env.wait.target_id == "audience"


@pytest.mark.parametrize(
    ("key", "first_run"),
    [
        ("niche", "Какая у вас Ниша"),
        ("audience", "Кто ваш читатель?"),
        ("project", "Куда приглашать читателей в конце Статьи?"),
    ],
)
async def test_every_typed_step_asks_in_first_run_words_not_the_settings_ones(
    env: Env, key: str, first_run: str
) -> None:
    """The Экран Настроек's question asks to *change* a value; the wizard asks for it."""
    setting = next(setting for setting in SETTING_FIELDS if setting.key == key)

    await env.press(key)

    question = env.last[1]
    assert first_run in question
    assert setting.question not in question
    assert "Например:" in question or "Подойдёт:" in question


async def test_a_step_without_its_own_wording_falls_back_to_the_settings_question() -> None:
    niche = next(setting for setting in SETTING_FIELDS if setting.key == "niche")
    extra = replace(niche, key="extra", question="✏️ Вопрос Экрана Настроек")
    env = Env(fields=(*onboarding_fields(), extra))

    await env.press("extra")

    assert "✏️ Вопрос Экрана Настроек" in env.last[1]


async def test_audience_answer_is_saved(env: Env) -> None:
    await env.press("audience")

    await env.answer("Владельцы кофеен, 30–45 лет, маркетингом занимаются сами")

    assert "кофеен" in (await env.settings.read(WS_A)).audience
    assert env.wait.target_id == "persona"


async def test_rejected_answer_keeps_the_question_and_its_buttons(env: Env) -> None:
    await env.press("start")
    question_id = env.wait.prompt_message_id

    await env.answer("   ")

    ((_, message_id, text, markup),) = env.bot.edited
    assert message_id == question_id
    assert text.startswith("⚠️ Ниша не может быть пустой.\n\nШаг 1 из 4 · Ниша")
    assert button_data(markup) == [["ob:intro"], [f"ci:{KIND}"]]
    assert env.wait.target_id == "niche"


async def test_persona_step_offers_the_presets_as_buttons(env: Env) -> None:
    await env.press("persona")

    _, question, markup, _ = env.last
    assert question.startswith("Шаг 3 из 4 · Стиль автора")
    assert "Выберите стиль кнопкой ниже или опишите свой" in question
    # A newcomer sees what each Пресет means, not just its title.
    assert "• Маркетолог-практик — практикующий маркетолог" in question
    data = _data(markup)
    persona = next(setting for setting in SETTING_FIELDS if setting.key == "persona")
    assert [f"op:persona:{index}" for index in range(len(persona.presets))] == data[
        : len(persona.presets)
    ]
    assert "ob:persona:c" in data and "ob:audience" in data and "ob:project" in data
    assert "✏️ Свой стиль" in [text for row in button_texts(markup) for text in row]


async def test_picking_a_preset_saves_it_and_moves_on(env: Env) -> None:
    await env.press("persona")
    persona = next(setting for setting in SETTING_FIELDS if setting.key == "persona")

    await env.onboarding.pick(WORKSPACE, PRIVATE, OWNER, 100, "persona:1")

    assert env.store.values["voice"] == persona.presets[1][1]
    assert env.wait.target_id == "project"
    assert env.last[1].startswith(f"✅ Стиль автора изменён: {persona.presets[1][0]}")


async def test_own_persona_asks_the_settings_question_and_back_returns_to_presets(
    env: Env,
) -> None:
    await env.press("persona")
    await env.press("persona:c")

    _, question, markup, _ = env.last
    assert "Опишите свой стиль" in question and "Роль:" in question
    assert button_data(markup)[0] == ["ob:persona", "ob:project"]

    await env.answer("Тон: дерзкий")  # no «Роль»

    assert "Не нашёл строку «Роль: …»" in env.bot.edited[-1][2]
    assert env.wait.target_id == "persona:c"


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
    assert "Ваш канал или сайт: не задан" in text
    assert button_data(markup) == [
        ["og:"],
        ["ob:niche:r"],
        ["ob:audience:r"],
        ["ob:persona:r"],
        ["ob:project:r"],
        ["gd:"],
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

    await env.onboarding.launch(WORKSPACE, PRIVATE, 77)
    await env.onboarding.launch(WORKSPACE, PRIVATE, 77)  # double tap

    assert env.menu.generated == [(WS_A, PRIVATE)]
    # The card says it all - no second «Генерирую План…» under it.
    assert env.menu.announced == [False]
    (_, _, launched, markup), (_, _, again, _) = env.bot.edited
    assert launched.startswith("🚀 Запускаю!")
    assert "План придёт сюда одним сообщением" in launched
    assert "Wordstat" not in launched and "Направлени" not in launched
    assert "среда, 10:30" in launched
    assert button_data(markup) == [["mn:"]]
    assert again.startswith("✅ Вводные сохранены. Первый План уже составляется")
    assert "придёт сюда" in again
    assert await env.state.needed(WS_A) is False


async def test_a_review_card_left_after_later_still_launches(env: Env) -> None:
    await env.press("review")
    await env.onboarding.later(WORKSPACE, PRIVATE, 100)

    await env.onboarding.launch(WORKSPACE, PRIVATE, 77)

    assert env.menu.generated == [(WS_A, PRIVATE)]
    assert env.bot.edited[-1][2].startswith("🚀 Запускаю!")


async def test_later_ends_the_wizard_and_shows_the_menu(env: Env) -> None:
    await env.onboarding.later(WORKSPACE, PRIVATE, 100)

    assert env.menu.shown == [(PRIVATE, 100)]
    assert await env.onboarding.offer(WORKSPACE, PRIVATE) is False


async def test_an_unfinished_wizard_is_offered_again_on_start(env: Env) -> None:
    await env.press("start")
    await env.answer("фитнес")

    assert await env.onboarding.offer(WORKSPACE, PRIVATE) is True


# --- interruptions ---


async def test_a_command_interrupts_the_open_question(env: Env) -> None:
    await env.press("start")
    question_id = env.wait.prompt_message_id

    await env.onboarding.interrupt(WORKSPACE, PRIVATE, OWNER)

    assert env.wait is None
    assert (PRIVATE, question_id) in env.bot.deleted
    assert await env.answer("просто текст") is False
    assert (await env.settings.read(WS_A)).niche == "маркетинг"


async def test_other_kinds_of_waits_are_not_taken(env: Env) -> None:
    await env.pending.put(WS_A, PRIVATE, OWNER, PendingInput("setting_input", "niche:5", 9))

    assert await env.answer("фитнес") is False


async def test_without_audience_the_wizard_has_three_steps() -> None:
    fields = [replace(setting) for setting in SETTING_FIELDS if setting.key != "audience"]
    env = Env(fields=onboarding_fields(fields))

    await env.press("start")
    await env.answer("фитнес")

    assert env.last[1].startswith("✅ Ниша изменена: фитнес\n\nШаг 2 из 3 · Стиль автора")


async def test_a_stale_start_button_after_finishing_only_shows_the_menu(env: Env) -> None:
    await env.onboarding.later(WORKSPACE, PRIVATE, 100)

    await env.onboarding.go(WORKSPACE, PRIVATE, OWNER, 100, "start")

    assert env.menu.shown == [(PRIVATE, 100)] * 2
    assert env.store.values["onboarding"] == "later" and env.wait is None


class _GuideStore:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def set(self, key: str, value: str) -> None:
        self.values[key] = value


async def test_with_pictures_the_intro_is_a_photo_with_the_same_text_and_buttons() -> None:
    """#114: the intro is a standalone message, so it gets its picture."""
    env = Env()
    env.onboarding = Onboarding(
        env.state,
        env.settings,
        InputPrompt(env.bot, env.pending),
        env.bot,
        env.menu,
        FakeSchedule(),
        photos=AssetPhotos(env.bot, _GuideStore()),
    )

    assert await env.onboarding.offer(WORKSPACE, PRIVATE) is True

    ((_, photo, caption, markup),) = env.bot.photos
    assert photo.filename == "onboarding.png"
    assert "4 коротких шага" in caption
    assert button_data(markup) == [["ob:start"], ["ox:"], ["gd:"]]
    assert env.bot.sent == []


async def test_one_workspaces_wizard_never_touches_anothers() -> None:
    """ADR-0015: A's answers and launch don't set up B, and B is still offered the wizard."""
    env = Env()
    other = Workspace(WS_B, 2, None, "Europe/Moscow", None, None)
    await env.press("start")
    await env.answer("фитнес")
    await env.onboarding.launch(WORKSPACE, PRIVATE, 77)

    assert env.store.by_workspace.get(WS_B, {}) == {}
    assert await env.onboarding.offer(other, 2) is True
    assert await env.onboarding.handle_reply(other, PRIVATE, OWNER, "юристы") is False
