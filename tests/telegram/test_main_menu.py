from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from content_zavod.scheduling import ScheduleConfig
from content_zavod.telegram import (
    PlanId,
    PlanItemId,
    PlanItemView,
    PlanMessageRef,
    PlanView,
    TelegramGateway,
)
from content_zavod.telegram.asset_photos import AssetPhotos
from content_zavod.telegram.input_prompt import InputPrompt
from content_zavod.telegram.main_menu import (
    TOPIC_INPUT_KIND,
    MainMenu,
    build_main_menu_keyboard,
    plan_message_link,
)

from .fakes import FakePendingInputs, RecordingBot, button_data, button_texts

USER = 5
TEAM_CHAT = -1001234567890
MOSCOW = ZoneInfo("Europe/Moscow")
NOW = datetime(2026, 8, 12, 12, 0, tzinfo=UTC)  # 2026-W33: 10–16 августа 2026


class FakePlan:
    def __init__(self, view: PlanView | None = None, ref: PlanMessageRef | None = None) -> None:
        self.view = view
        self.ref = ref
        self.requested: list[str] = []
        self.added: list[tuple[str, list]] = []

    async def find_active(self, week_label: str) -> PlanView | None:
        return self.view

    async def get_message_ref(self, plan_id: PlanId) -> PlanMessageRef | None:
        return self.ref

    async def request_new(self, week_label: str, *, generation_id: str | None = None) -> int:
        self.requested.append(week_label)
        return 1

    async def recent_topic_titles(self, since: datetime) -> list[str]:
        return []

    async def add_topics(self, week_label: str, topics: list) -> PlanId:
        self.added.append((week_label, topics))
        self.view = PlanView(
            id=PlanId("p"),
            week_label=week_label,
            items=[
                PlanItemView(id=PlanItemId("i"), title=topics[0].title, status="pending_review")
            ],
        )
        return PlanId("p")

    async def get(self, plan_id: PlanId) -> PlanView:
        assert self.view is not None
        return self.view

    async def record_message_ref(self, plan_id: PlanId, chat_id: int, message_id: int) -> None:
        self.ref = PlanMessageRef(chat_id=chat_id, message_id=message_id)

    async def archive(self, plan_id: PlanId) -> None: ...

    async def request_replacement(self, plan_id: PlanId) -> None: ...


class FakeQueue:
    async def get_status(self, job_id: int) -> str:
        return "queued"

    async def retry(self, job_id: int) -> bool:
        return True


class FakeSchedule:
    def __init__(self, config: ScheduleConfig | None = None) -> None:
        self.config = config

    async def get(self) -> ScheduleConfig | None:
        return self.config

    async def set(self, day_of_week: str, hour: int, minute: int) -> None: ...


class Env:
    def __init__(self, plan: FakePlan | None = None) -> None:
        self.bot = RecordingBot()
        self.pending = FakePendingInputs()
        self.plan = plan or FakePlan()
        self.menu = MainMenu(
            self.plan,
            FakeQueue(),
            FakeSchedule(ScheduleConfig("wed", 10, 0)),
            self.bot,
            TelegramGateway(self.bot),
            InputPrompt(self.bot, self.pending),
            team_chat_id=TEAM_CHAT,
            tz=MOSCOW,
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


def test_content_manager_menu_has_the_shared_buttons_and_the_guide() -> None:
    markup = build_main_menu_keyboard("content_manager")

    assert button_texts(markup) == [
        ["📋 План недели"],
        ["✍️ Предложить Тему"],
        ["🗂 История"],
        ["📖 Как пользоваться"],
    ]
    # The carousel takes the menu's place (#114).
    assert button_data(markup) == [["mp:"], ["mt:"], ["mh:"], ["gd:m"]]


def test_owner_menu_adds_settings_members_schedule_and_team_note() -> None:
    markup = build_main_menu_keyboard("owner")

    assert button_texts(markup)[3:] == [
        ["⚙️ Настройки"],
        ["👥 Участники"],
        ["🕘 Расписание"],
        ["📌 Памятка в чат команды"],
        ["📖 Как пользоваться"],
    ]
    assert button_data(markup)[3:] == [["st:"], ["mm:"], ["sc:m"], ["gp:"], ["gd:m"]]


async def test_start_menu_greets_then_shows_the_menu() -> None:
    env = Env()

    await env.menu.send(USER, "owner", welcome=True)

    ((_, text, markup, _),) = env.bot.sent
    assert text.startswith("👋 Добро пожаловать!")
    assert text.endswith("🏠 Главное меню\nВыберите, что сделать:")
    assert len(markup.inline_keyboard) == 8


async def test_show_edits_back_to_the_menu_in_place() -> None:
    env = Env()

    await env.menu.show(USER, 42, "content_manager")

    ((_, message_id, text, _),) = env.bot.edited
    assert (message_id, text) == (42, "🏠 Главное меню\nВыберите, что сделать:")


@pytest.mark.parametrize(
    ("chat_id", "expected"),
    [(-1001234567890, "https://t.me/c/1234567890/77"), (-4567, None), (5, None)],
)
def test_plan_message_link_only_for_supergroups(chat_id, expected) -> None:
    assert plan_message_link(PlanMessageRef(chat_id=chat_id, message_id=77)) == expected


async def test_plan_of_the_week_links_to_its_message_in_the_team_chat() -> None:
    plan = FakePlan(
        make_view("pending_review", "approved", "rejected"),
        PlanMessageRef(chat_id=TEAM_CHAT, message_id=77),
    )
    env = Env(plan)

    await env.menu.show_plan(USER, 42)

    ((_, message_id, text, markup),) = env.bot.edited
    assert message_id == 42
    assert text.startswith("📋 План на 10–16 августа 2026\nТем: 2 — ждёт согласования.")
    assert button_texts(markup) == [["📋 Открыть План"], ["◀ Назад"]]
    assert markup.inline_keyboard[0][0].url == "https://t.me/c/1234567890/77"


async def test_plan_without_a_linkable_message_says_where_it_is() -> None:
    env = Env(FakePlan(make_view("approved"), PlanMessageRef(chat_id=-4567, message_id=1)))

    await env.menu.show_plan(USER, 42)

    _, _, text, markup = env.bot.edited[0]
    assert "Тем: 1 — утверждён." in text
    assert "найдите там сообщение с Планом" in text
    assert button_texts(markup) == [["◀ Назад"]]


async def test_no_plan_yet_explains_the_schedule_and_offers_to_make_one() -> None:
    env = Env()

    await env.menu.show_plan(USER, 42)

    _, _, text, markup = env.bot.edited[0]
    assert text.startswith("📋 Плана на 10–16 августа 2026 пока нет.")
    assert "по расписанию: среда, 10:00" in text
    assert button_texts(markup) == [["🪄 Составить План"], ["◀ Назад"]]
    assert button_data(markup) == [["mg:"], ["mn:"]]


async def test_make_plan_button_runs_generate_plan() -> None:
    env = Env()

    await env.menu.generate_plan(USER)

    assert env.plan.requested == ["2026-W33"]
    assert "Он появится в чате команды" in env.bot.sent[0][1]


async def test_propose_topic_asks_then_adds_the_answer_to_the_plan() -> None:
    env = Env()

    await env.menu.ask_topic(USER, USER)
    assert "Например:" in env.bot.sent[0][1]
    assert env.pending.rows[(USER, USER)].kind == TOPIC_INPUT_KIND

    assert await env.menu.handle_reply(USER, USER, "Как считать ROI", None) is True

    assert env.plan.added[0][1][0].title == "Как считать ROI"
    assert (USER, 100) in env.bot.deleted  # the question is cleaned up
    assert env.bot.sent[-1][1] == "Тема добавлена в План — он в чате команды."


async def test_reply_without_a_topic_question_is_not_consumed() -> None:
    env = Env()

    assert await env.menu.handle_reply(USER, USER, "привет", None) is False


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
        team_chat_id=TEAM_CHAT,
        tz=MOSCOW,
        photos=AssetPhotos(env.bot, _GuideStore()),
    )

    await menu.send(USER, "owner", welcome=True)

    ((_, photo, caption, _),) = env.bot.photos
    assert photo.filename == "welcome.png" and caption.startswith("👋 Добро пожаловать!")
    ((_, text, markup, _),) = env.bot.sent
    assert text == "🏠 Главное меню\nВыберите, что сделать:"
    assert markup is not None


async def test_show_on_a_photo_message_sends_the_menu_below_it() -> None:
    env = Env()
    env.bot.fail_edits = True  # a photo (the КМ welcome) can't become a text message

    await env.menu.show(USER, 42, "content_manager")

    ((_, text, _, _),) = env.bot.sent
    assert text == "🏠 Главное меню\nВыберите, что сделать:"
