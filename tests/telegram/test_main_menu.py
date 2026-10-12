from __future__ import annotations

from datetime import UTC, datetime

from content_zavod.domain import PlanHubView
from content_zavod.scheduling import ScheduleConfig
from content_zavod.settings import OwnerSettings
from content_zavod.telegram import (
    PlanId,
    PlanItemId,
    PlanItemView,
    PlanMessageRef,
    PlanSummary,
    PlanView,
    TelegramGateway,
)
from content_zavod.telegram.asset_photos import AssetPhotos
from content_zavod.telegram.input_prompt import InputPrompt
from content_zavod.telegram.main_menu import TOPIC_INPUT_KIND, MainMenu, build_main_menu_keyboard
from content_zavod.telegram.topic_command import TOPIC_MAX_LENGTH
from content_zavod.workspaces import Workspace
from tests.tenancy import WS_A

from .fakes import FakePendingInputs, RecordingBot, button_data, button_texts

USER = 5
WORKSPACE = Workspace(WS_A, USER, None, "Europe/Moscow", None, None)
NOW = datetime(2026, 8, 12, 12, 0, tzinfo=UTC)  # 2026-W33: 10–16 августа 2026


class FakePlan:
    def __init__(self, view: PlanView | None = None, ref: PlanMessageRef | None = None) -> None:
        self.view = view
        self.ref = ref
        self.requested: list[str] = []
        self.added: list[tuple[str, list]] = []
        self.status = "pending_review"

    async def find_active(self, workspace_id: int, week_label: str) -> PlanView | None:
        return self.view

    async def get_message_ref(self, workspace_id: int, plan_id: PlanId) -> PlanMessageRef | None:
        return self.ref

    async def request_new(
        self, workspace_id: int, week_label: str, *, generation_id: str | None = None
    ) -> int:
        self.requested.append(week_label)
        return 1

    async def recent_topic_titles(self, workspace_id: int, since: datetime) -> list[str]:
        return []

    async def add_topics(self, workspace_id: int, week_label: str, topics: list) -> PlanId:
        self.added.append((week_label, topics))
        self.view = PlanView(
            id=PlanId("p"),
            week_label=week_label,
            items=[
                PlanItemView(id=PlanItemId("i"), title=topics[0].title, status="pending_review")
            ],
        )
        return PlanId("p")

    async def get(self, workspace_id: int, plan_id: PlanId) -> PlanView:
        assert self.view is not None
        return self.view

    async def get_summary(self, workspace_id: int, plan_id: PlanId) -> PlanSummary:
        return PlanSummary(id=plan_id, week_label="2026-W33", status=self.status)

    async def record_message_ref(
        self, workspace_id: int, plan_id: PlanId, chat_id: int, message_id: int
    ) -> None:
        if self.ref is None:
            self.ref = PlanMessageRef(chat_id=chat_id, message_id=message_id)

    async def replace_message_ref(
        self, workspace_id: int, plan_id: PlanId, old, chat_id: int, message_id: int
    ) -> bool:
        if self.ref != old:
            return False
        self.ref = PlanMessageRef(chat_id=chat_id, message_id=message_id)
        return True

    async def archive(self, workspace_id: int, plan_id: PlanId) -> None: ...

    async def request_replacement(self, workspace_id: int, plan_id: PlanId) -> None: ...


class FakeQueue:
    async def get_status(self, workspace_id: int, job_id: int) -> str:
        return "queued"

    async def retry(self, workspace_id: int, job_id: int) -> bool:
        return True


class FakeSchedule:
    def __init__(self, config: ScheduleConfig | None = None) -> None:
        self.config = config

    async def get(self, workspace_id: int) -> ScheduleConfig | None:
        return self.config

    async def set(self, workspace_id: int, day_of_week: str, hour: int, minute: int) -> None: ...


class FakeSettings:
    """`niche_saved` - whether the Владелец has told the Ниша."""

    def __init__(self) -> None:
        self.niche_saved = True

    async def read(self, workspace_id: int) -> OwnerSettings:
        return OwnerSettings(
            niche="кофейни",
            persona=None,
            custom_persona=None,
            niche_saved=self.niche_saved,
        )


class FakeOnboarding:
    def __init__(self) -> None:
        self.mid_way = False

    async def needed(self, workspace_id: int) -> bool:
        return self.mid_way


class Env:
    def __init__(self, plan: FakePlan | None = None) -> None:
        self.bot = RecordingBot()
        self.pending = FakePendingInputs()
        self.plan = plan or FakePlan()
        self.settings = FakeSettings()
        self.onboarding = FakeOnboarding()
        self.menu = MainMenu(
            self.plan,
            FakeQueue(),
            FakeSchedule(ScheduleConfig("wed", 10, 0)),
            self.bot,
            TelegramGateway(self.bot),
            InputPrompt(self.bot, self.pending),
            settings=self.settings,
            onboarding=self.onboarding,
            now=lambda: NOW,
        )


def make_view(*statuses: str) -> PlanView:
    return PlanView(
        id=PlanId("p"),
        week_label="2026-W33",
        items=[
            PlanItemView(id=PlanItemId(f"i{n}"), title=f"Тема {n}", status=status)
            for n, status in enumerate(statuses)
        ],
    )


def test_everyone_gets_the_same_menu() -> None:
    """ADR-0015: no Роли - every user is the Владелец of their own Пространство."""
    markup = build_main_menu_keyboard()

    assert button_texts(markup) == [
        ["📋 План недели"],
        ["✍️ Предложить Тему"],
        ["🗂 История"],
        ["⚙️ Настройки"],
        ["🕘 Расписание"],
        ["📖 Как пользоваться"],
    ]
    # The carousel takes the menu's place (#114).
    assert button_data(markup) == [["mp:"], ["mt:"], ["mh:"], ["st:"], ["sc:m"], ["gd:m"]]


async def test_start_menu_greets_then_shows_the_menu() -> None:
    env = Env()

    await env.menu.send(USER, welcome=True)

    ((_, text, markup, _),) = env.bot.sent
    assert text.startswith("👋 Добро пожаловать!")
    assert text.endswith("🏠 Главное меню\nВыберите, что сделать:")
    assert len(markup.inline_keyboard) == 6


async def test_show_edits_back_to_the_menu_in_place() -> None:
    env = Env()

    await env.menu.show(USER, 42)

    ((_, message_id, text, _),) = env.bot.edited
    assert (message_id, text) == (42, "🏠 Главное меню\nВыберите, что сделать:")


async def test_plan_of_the_week_is_sent_again_below_and_the_old_copy_goes() -> None:
    """#118: a private chat has no link to an older message - the Plan moves to the bottom."""
    plan = FakePlan(
        make_view("pending_review", "approved", "rejected"),
        PlanMessageRef(chat_id=USER, message_id=7),
    )
    env = Env(plan)

    await env.menu.show_plan(WORKSPACE, USER, 42)

    ((_, message_id, text, markup),) = env.bot.edited
    assert message_id == 42
    assert text.startswith("📋 План на 10–16 августа 2026\nТем: 2 — ждёт согласования.")
    assert "ниже" in text
    assert button_texts(markup) == [["◀ Назад"]]
    ((chat_id, plan_text, _, _),) = env.bot.sent
    assert chat_id == USER and "Тема 0" in plan_text
    assert plan.ref == PlanMessageRef(chat_id=USER, message_id=100)
    assert env.bot.deleted == [(USER, 7)]


async def test_an_approved_plan_comes_back_as_its_hub() -> None:
    plan = FakePlan(make_view("approved"), PlanMessageRef(chat_id=USER, message_id=7))
    plan.status = "approved"

    async def get_hub(workspace_id, plan_id):
        return PlanHubView(id=plan_id, week_label="2026-W33", status="approved", topics=[])

    plan.get_hub = get_hub  # type: ignore[attr-defined]
    env = Env(plan)

    await env.menu.show_plan(WORKSPACE, USER, 42)

    assert "Тем: 1 — утверждён." in env.bot.edited[0][2]
    assert plan.ref == PlanMessageRef(chat_id=USER, message_id=100)


async def test_no_plan_yet_explains_the_schedule_and_offers_to_make_one() -> None:
    env = Env()

    await env.menu.show_plan(WORKSPACE, USER, 42)

    _, _, text, markup = env.bot.edited[0]
    assert text.startswith("📋 Плана на 10–16 августа 2026 пока нет.")
    assert "по расписанию: среда, 10:00" in text
    assert button_texts(markup) == [["🪄 Составить План"], ["◀ Назад"]]
    assert button_data(markup) == [["mg:"], ["mn:"]]
    assert env.bot.sent == []


async def test_no_plan_and_no_niche_asks_for_the_niche_not_the_schedule() -> None:
    """#118: the Расписание won't make a Plan without a Ниша - don't promise it will."""
    env = Env()
    env.settings.niche_saved = False

    await env.menu.show_plan(WORKSPACE, USER, 42)

    _, _, text, markup = env.bot.edited[0]
    assert text.startswith("Сначала скажите, о чём ваш блог")
    assert "по расписанию" not in text
    assert button_data(markup) == [["sa:niche"], ["mn:"]]


async def test_no_plan_mid_wizard_promises_no_schedule() -> None:
    """The tick skips a Пространство whose wizard is mid-way, so no time is promised."""
    env = Env()
    env.onboarding.mid_way = True

    await env.menu.show_plan(WORKSPACE, USER, 42)

    _, _, text, markup = env.bot.edited[0]
    assert text == "📋 Плана на 10–16 августа 2026 пока нет. Можно составить его прямо сейчас."
    assert button_data(markup) == [["mg:"], ["mn:"]]


async def test_a_plan_with_every_topic_removed_is_said_empty_and_not_sent_again() -> None:
    plan = FakePlan(make_view("rejected"), PlanMessageRef(chat_id=USER, message_id=7))
    env = Env(plan)

    await env.menu.show_plan(WORKSPACE, USER, 42)

    _, _, text, markup = env.bot.edited[0]
    assert text.startswith("📋 План на 10–16 августа 2026 пока пуст")
    assert button_data(markup) == [["mt:"], ["mn:"]]
    assert env.bot.sent == [] and env.bot.deleted == []


async def test_make_plan_button_without_a_niche_asks_for_it() -> None:
    env = Env()
    env.settings.niche_saved = False

    await env.menu.generate_plan(WORKSPACE, USER)

    assert env.plan.requested == []
    assert env.bot.sent[0][1].startswith("Сначала скажите, о чём ваш блог")


async def test_make_plan_button_runs_generate_plan() -> None:
    env = Env()

    await env.menu.generate_plan(WORKSPACE, USER)

    assert env.plan.requested == ["2026-W33"]
    assert env.bot.sent[0][1] == "Генерирую План на 10–16 августа 2026..."


async def test_propose_topic_asks_then_adds_the_answer_to_the_plan() -> None:
    env = Env()

    await env.menu.ask_topic(WORKSPACE, USER, USER)
    assert "Например:" in env.bot.sent[0][1]
    assert env.pending.rows[(USER, USER)].kind == TOPIC_INPUT_KIND

    assert await env.menu.handle_reply(WORKSPACE, USER, USER, "Как считать ROI") is True

    assert env.plan.added[0][1][0].title == "Как считать ROI"
    assert (USER, 100) in env.bot.deleted  # the question is cleaned up
    # The Plan message itself arrives in the private chat.
    assert "Как считать ROI" in env.bot.sent[-1][1]


async def test_an_overlong_topic_is_refused_and_the_question_stays_open() -> None:
    """ADR-0015: past `TOPIC_MAX_LENGTH` nothing is added; the same question asks again."""
    env = Env()
    await env.menu.ask_topic(WORKSPACE, USER, USER)

    assert await env.menu.handle_reply(WORKSPACE, USER, USER, "я" * (TOPIC_MAX_LENGTH + 1))

    assert env.plan.added == []
    assert env.pending.rows[(USER, USER)].kind == TOPIC_INPUT_KIND
    assert str(TOPIC_MAX_LENGTH) in env.bot.edited[-1][2]

    assert await env.menu.handle_reply(WORKSPACE, USER, USER, "Как считать ROI")
    assert env.plan.added[0][1][0].title == "Как считать ROI"


async def test_reply_without_a_topic_question_is_not_consumed() -> None:
    env = Env()

    assert await env.menu.handle_reply(WORKSPACE, USER, USER, "привет") is False


class _GuideStore:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def set(self, key: str, value: str) -> None:
        self.values[key] = value


# --- #114: the welcome picture and a menu that can't replace a photo ---


async def test_start_with_pictures_greets_with_a_photo_then_sends_the_menu() -> None:
    env = Env()
    menu = MainMenu(
        env.plan,
        FakeQueue(),
        FakeSchedule(),
        env.bot,
        TelegramGateway(env.bot),
        InputPrompt(env.bot, env.pending),
        settings=env.settings,
        onboarding=env.onboarding,
        photos=AssetPhotos(env.bot, _GuideStore()),
    )

    await menu.send(USER, welcome=True)

    ((_, photo, caption, _),) = env.bot.photos
    assert photo.filename == "welcome.png" and caption.startswith("👋 Добро пожаловать!")
    ((_, text, markup, _),) = env.bot.sent
    assert text == "🏠 Главное меню\nВыберите, что сделать:"
    assert markup is not None


async def test_show_on_a_photo_message_sends_the_menu_below_it() -> None:
    env = Env()
    env.bot.fail_edits = True  # a photo (the onboarding intro) can't become a text message

    await env.menu.show(USER, 42)

    ((_, text, _, _),) = env.bot.sent
    assert text == "🏠 Главное меню\nВыберите, что сделать:"
