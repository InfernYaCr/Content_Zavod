from __future__ import annotations

from datetime import UTC, datetime

import asyncpg

from content_zavod.domain import HubArticleCell, HubTopic, Plan, PlanHubView, TopicDraft
from content_zavod.job_queue import JobQueue
from content_zavod.owner_settings import WorkspaceSettingsStore
from content_zavod.scheduling import ScheduleConfig, ScheduleSettings
from content_zavod.settings import OnboardingState, OwnerSettings, SettingsService
from content_zavod.telegram import (
    ArticleId,
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
from content_zavod.telegram.texts import menu_limit_text
from content_zavod.telegram.topic_command import TOPIC_MAX_LENGTH
from content_zavod.workspaces import GenerationBusy, Limits, TrialLimits, Usage, Workspace
from tests.tenancy import WS_A, WS_B

from .fakes import FakeLimits, FakePendingInputs, RecordingBot, button_data, button_texts

USER = 5
WORKSPACE = Workspace(WS_A, USER, None, "Europe/Moscow", None, None)
NOW = datetime(2026, 8, 12, 12, 0, tzinfo=UTC)  # 2026-W33: 10–16 августа 2026
WED_10 = ScheduleConfig("wed", 10, 0)


class FakePlan:
    def __init__(self, view: PlanView | None = None, ref: PlanMessageRef | None = None) -> None:
        self.view = view
        self.hub: PlanHubView | None = None
        self.ref = ref
        self.requested: list[str] = []
        self.added: list[tuple[str, list]] = []
        self.status = "pending_review"

    async def find_active(self, workspace_id: int, week_label: str) -> PlanView | None:
        return self.view

    async def get_hub(self, workspace_id: int, plan_id: PlanId) -> PlanHubView:
        assert self.hub is not None
        return self.hub

    async def get_message_ref(self, workspace_id: int, plan_id: PlanId) -> PlanMessageRef | None:
        return self.ref

    async def request_new(
        self, workspace_id: int, week_label: str, *, generation_id: str | None = None, conn=None
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
    async def get_status(self, workspace_id: int, job_id: int, *, conn=None) -> str:
        return "queued"

    async def retry(self, workspace_id: int, job_id: int, *, conn=None) -> bool:
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
        self.niche = "кофейни"

    async def read(self, workspace_id: int) -> OwnerSettings:
        return OwnerSettings(
            niche=self.niche,
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
    def __init__(
        self,
        plan: FakePlan | None = None,
        *,
        workspace: Workspace = WORKSPACE,
        now: datetime = NOW,
        schedule: ScheduleConfig | None = WED_10,
    ) -> None:
        self.bot = RecordingBot()
        self.pending = FakePendingInputs()
        self.plan = plan or FakePlan()
        self.settings = FakeSettings()
        self.onboarding = FakeOnboarding()
        self.limits = FakeLimits()
        self.menu = MainMenu(
            self.plan,
            FakeQueue(),
            FakeSchedule(schedule),
            self.bot,
            TelegramGateway(self.bot),
            InputPrompt(self.bot, self.pending),
            settings=self.settings,
            onboarding=self.onboarding,
            limits=self.limits,
            now=lambda: now,
        )
        self.workspace = workspace

    async def menu_text(self) -> str:
        await self.menu.show(self.workspace, USER, 42)
        return self.bot.edited[-1][2]


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
    # The Инструкция takes the menu's place (#114).
    assert button_data(markup) == [["mp:"], ["mt:"], ["mh:"], ["st:"], ["sc:m"], ["gd:m"]]


# The text of a new Пространство's menu at `NOW` (Wed 15:00 МСК, the Расписание on Wed 10:00).
DEFAULT_MENU = (
    "🏠 Главное меню\n\n"
    "📋 Плана на эту неделю пока нет\n\n"
    "🕘 Следующий План — ср, 19 августа, 10:00\n\n"
    "🎁 Пробный доступ\n"
    "• Планов: использовано 0 из 1\n"
    "• Статей: осталось 8 из 8\n"
    "Одна Статья = одна площадка (Дзен или VC.ru).\n"
    "«✏️ Доработать» тоже тратит одну Статью.\n\n"
    "🎯 Ниша: кофейни"
)


def make_hub(*titles: str, started: bool = True, ready: int = 0, exported: int = 0) -> PlanHubView:
    """An approved Plan's Хаб: a Тема per title, two Статьи each; the first `ready` of them
    ready, the first `exported` of those marked 👍; `started=False` - no Статья row yet."""
    cells = iter(range(len(titles) * 2))

    def cell(platform: str) -> HubArticleCell:
        n = next(cells)
        if not started:
            return HubArticleCell(platform=platform, state="pending")
        return HubArticleCell(
            platform=platform,
            state="ready" if n < ready else "pending",
            article_id=ArticleId(f"a{n}"),
            exported=n < exported,
        )

    return PlanHubView(
        id=PlanId("p"),
        week_label="2026-W33",
        status="approved",
        topics=[
            HubTopic(
                id=PlanItemId(f"i{n}"),
                number=n + 1,
                title=title,
                cover=None,
                has_cover=False,
                articles=[cell("zen"), cell("vc")],
            )
            for n, title in enumerate(titles)
        ],
    )


def approved_env(hub: PlanHubView, **kwargs) -> Env:
    plan = FakePlan(make_view(*["approved"] * len(hub.topics)))
    plan.hub = hub
    return Env(plan, **kwargs)


async def test_start_menu_greets_then_shows_the_menu() -> None:
    env = Env()

    await env.menu.send(WORKSPACE, USER, welcome=True)

    ((_, text, markup, _),) = env.bot.sent
    assert text.startswith("👋 Добро пожаловать!")
    assert text.endswith(DEFAULT_MENU)
    assert len(markup.inline_keyboard) == 6


async def test_show_edits_back_to_the_menu_in_place() -> None:
    env = Env()

    await env.menu.show(WORKSPACE, USER, 42)

    ((_, message_id, text, _),) = env.bot.edited
    assert message_id == 42
    assert text == DEFAULT_MENU


async def test_the_menu_sums_up_the_week_the_next_plan_the_limit_and_the_niche() -> None:
    """#149: the owner-approved text - Темы cut to 40 characters, at most 3, Статьи counted
    from the Хаб (👍 - exported)."""
    hub = make_hub(
        "Как выбрать кофемашину для маленькой кофейни у дома",
        "Сезонное меню",
        "Латте-арт",
        "Чек-лист бариста",
        ready=4,
        exported=2,
    )
    env = approved_env(hub)
    env.limits.usage_value = Usage(plans_used=1, plan_limit=2, articles_used=6, article_limit=8)
    env.limits.contact = "@owner"
    env.settings.niche = "домашняя выпечка"

    assert await env.menu_text() == (
        "🏠 Главное меню\n\n"
        "📋 Эта неделя (10–16 августа)\n"
        "«Как выбрать кофемашину для маленькой ко…», «Сезонное меню», «Латте-арт» и ещё 1\n"
        "Статьи: готово 4 из 8 · 👍 отмечено 2\n\n"
        "🕘 Следующий План — ср, 19 августа, 10:00\n\n"
        "🎁 Пробный доступ\n"
        "• Планов: использован 1 из 2\n"
        "• Статей: осталось 2 из 8\n"
        "Одна Статья = одна площадка (Дзен или VC.ru).\n"
        "«✏️ Доработать» тоже тратит одну Статью.\n"
        "Нужно больше — напишите @owner\n\n"
        "🎯 Ниша: домашняя выпечка"
    )


async def test_three_topics_are_named_without_and_more() -> None:
    env = approved_env(make_hub("А", "Б", "В"))

    assert "\n«А», «Б», «В»\n" in await env.menu_text()


async def test_articles_not_started_yet_leave_out_their_line() -> None:
    env = approved_env(make_hub("А", started=False))

    text = await env.menu_text()

    assert "📋 Эта неделя (10–16 августа)\n«А»\n\n🕘" in text
    assert "Статьи:" not in text


async def test_a_plan_waiting_for_approval_says_so_in_one_line() -> None:
    env = Env(FakePlan(make_view("pending_review", "pending_review", "approved", "rejected")))

    text = await env.menu_text()

    assert "\n\n📋 Эта неделя: План ждёт вашего утверждения (3 Темы)\n\n" in text
    # The week has its Plan: the Расписание makes the next one next week.
    assert "🕘 Следующий План — ср, 19 августа, 10:00" in text


async def test_no_plan_before_this_weeks_deadline_names_this_weeks_deadline() -> None:
    env = Env(now=datetime(2026, 8, 10, 6, 0, tzinfo=UTC))  # Mon 09:00 МСК

    text = await env.menu_text()

    assert "📋 Плана на эту неделю пока нет\n\n🕘 Следующий План — ср, 12 августа, 10:00" in text


async def test_a_plan_made_before_the_deadline_moves_the_next_one_to_next_week() -> None:
    env = Env(FakePlan(make_view("pending_review")), now=datetime(2026, 8, 10, 6, 0, tzinfo=UTC))

    assert "🕘 Следующий План — ср, 19 августа, 10:00" in await env.menu_text()


async def test_the_next_plan_is_in_the_workspaces_own_time_zone() -> None:
    """Mon 03:00 UTC: 06:00 МСК, before the Mon 09:00 deadline; 13:00 МСК+7, already past it."""
    now = datetime(2026, 10, 12, 3, 0, tzinfo=UTC)
    schedule = ScheduleConfig("mon", 9, 0)
    moscow = Env(now=now, schedule=schedule)
    vladivostok = Env(
        now=now,
        schedule=schedule,
        workspace=Workspace(WS_A, USER, None, "Asia/Vladivostok", None, None),
    )

    assert "🕘 Следующий План — пн, 12 октября, 09:00" in await moscow.menu_text()
    assert "🕘 Следующий План — пн, 19 октября, 09:00" in await vladivostok.menu_text()


async def test_without_a_schedule_row_the_default_one_is_named() -> None:
    env = Env(schedule=None)

    assert "🕘 Следующий План — пн, 17 августа, 09:00" in await env.menu_text()


async def test_no_niche_says_where_to_set_it_and_promises_no_plan() -> None:
    """The Расписание makes no Plan without a Ниша (#118): no «🕘» line."""
    env = Env()
    env.settings.niche_saved = False

    text = await env.menu_text()

    assert text.endswith("🎯 Ниша: не задана — укажите в ⚙️ Настройках")
    assert "🕘" not in text


async def test_mid_wizard_promises_no_plan() -> None:
    env = Env()
    env.onboarding.mid_way = True

    text = await env.menu_text()

    assert "🕘" not in text
    assert text.endswith("🎯 Ниша: кофейни")


async def test_used_up_plans_promise_no_next_plan_while_articles_are_left() -> None:
    """The tick enqueues nothing once the Лимит of Планов is used up - no «🕘» line."""
    env = Env()
    env.limits.usage_value = Usage(plans_used=1, plan_limit=1, articles_used=2, article_limit=8)

    text = await env.menu_text()

    assert "🕘" not in text
    assert "• Планов: использован 1 из 1\n• Статей: осталось 6 из 8" in text


async def test_used_up_plans_and_articles_promise_no_next_plan() -> None:
    env = Env()
    env.limits.usage_value = Usage(plans_used=1, plan_limit=1, articles_used=8, article_limit=8)

    text = await env.menu_text()

    assert "🕘" not in text
    assert "🎁 Пробный доступ закончился." in text


async def test_a_used_up_limit_is_one_line_with_the_contact() -> None:
    env = Env()
    env.limits.usage_value = Usage(plans_used=1, plan_limit=1, articles_used=9, article_limit=8)
    env.limits.contact = "@owner"

    text = await env.menu_text()

    assert "\n\n🎁 Пробный доступ закончился — напишите @owner\n\n🎯" in text
    assert "Планов:" not in text


async def test_an_administrators_limit_is_not_called_a_trial() -> None:
    env = Env()
    env.limits.usage_value = Usage(
        plans_used=0, plan_limit=5, articles_used=0, article_limit=40, custom=True
    )

    text = await env.menu_text()

    assert "\n\n🎁 Доступ\n• Планов: использовано 0 из 5\n• Статей: осталось 40 из 40\n" in text


def test_limit_block_declines_used_and_hides_an_empty_contact() -> None:
    def block(plans_used: int, contact: str | None = None, **kwargs) -> str:
        usage = Usage(
            plans_used=plans_used, plan_limit=30, articles_used=0, article_limit=8, **kwargs
        )
        return menu_limit_text(usage, contact)

    assert "• Планов: использован 1 из 30" in block(1)
    assert "• Планов: использован 21 из 30" in block(21)
    assert "• Планов: использовано 0 из 30" in block(0)
    assert "• Планов: использовано 2 из 30" in block(2)
    assert "• Планов: использовано 11 из 30" in block(11)
    assert "Нужно больше" not in block(0)
    assert block(0, "@owner").endswith("\nНужно больше — напишите @owner")
    used_up = Usage(plans_used=1, plan_limit=1, articles_used=8, article_limit=8, custom=True)
    assert menu_limit_text(used_up, None) == "🎁 Доступ закончился."


async def test_the_longest_menu_fits_one_message() -> None:
    hub = make_hub(*["Т" * 300] * 50, ready=100, exported=100)
    env = approved_env(hub)
    env.limits.contact = "@" + "к" * 100
    env.settings.niche = "н" * 200  # NICHE_MAX_LENGTH

    assert len(await env.menu_text()) <= 4096


async def test_make_plan_refused_by_the_limit_says_why_and_requests_nothing() -> None:
    env = Env()
    env.limits.refusal = GenerationBusy()

    await env.menu.generate_plan(WORKSPACE, USER)

    assert env.plan.requested == []
    ((_, text, _, _),) = env.bot.sent
    assert text == "⏳ Дождитесь окончания текущей генерации — потом можно продолжить."


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
        limits=env.limits,
        photos=AssetPhotos(env.bot, _GuideStore()),
    )

    await menu.send(WORKSPACE, USER, welcome=True)

    ((_, photo, caption, _),) = env.bot.photos
    assert photo.filename == "welcome.png" and caption.startswith("👋 Добро пожаловать!")
    ((_, text, markup, _),) = env.bot.sent
    assert text.startswith("🏠 Главное меню\n\n📋 Плана на эту неделю пока нет")
    assert markup is not None


async def test_show_on_a_photo_message_sends_the_menu_below_it() -> None:
    env = Env()
    env.bot.fail_edits = True  # a photo (the onboarding intro) can't become a text message

    await env.menu.show(WORKSPACE, USER, 42)

    ((_, text, _, _),) = env.bot.sent
    assert text == DEFAULT_MENU


# --- #149: the menu reads only the Пространство it was opened in ---


async def test_menu_of_a_shows_none_of_bs_plan_limit_or_niche(pool: asyncpg.Pool) -> None:
    """A real database: B's approved Plan, its own Лимит and Ниша never reach A's menu."""
    await pool.execute(
        "TRUNCATE TABLE jobs, article_versions, articles, plan_items, plans, generation_steps, "
        "limit_charges CASCADE"
    )
    await pool.execute(
        "DELETE FROM workspace_settings WHERE workspace_id = ANY($1::bigint[])", [WS_A, WS_B]
    )
    await pool.execute(
        "UPDATE workspaces SET plan_limit = NULL, article_limit = NULL WHERE id = ANY($1::bigint[])",
        [WS_A, WS_B],
    )
    queue = JobQueue(pool)
    plan = Plan(pool, queue)
    store = WorkspaceSettingsStore(pool)
    settings = SettingsService(store)
    bot = RecordingBot()
    menu = MainMenu(
        plan,
        queue,
        ScheduleSettings(pool),
        bot,
        TelegramGateway(bot),
        InputPrompt(bot, FakePendingInputs()),
        settings=settings,
        onboarding=OnboardingState(store),
        limits=Limits(pool, TrialLimits(contact="@owner")),
        now=lambda: NOW,
    )
    workspace_b = Workspace(WS_B, USER, None, "Europe/Moscow", None, None)

    async def menu_text(workspace: Workspace) -> str:
        await menu.show(workspace, USER, 42)
        return bot.edited[-1][2]

    before = await menu_text(WORKSPACE)
    plan_id = await plan.add_topics(WS_B, "2026-W33", [TopicDraft(title="Тема B")])
    await plan.approve_all(WS_B, plan_id)
    await settings.set_niche(WS_B, "юристы B")
    await pool.execute("UPDATE workspaces SET plan_limit = 5 WHERE id = $1", WS_B)
    try:
        after = await menu_text(WORKSPACE)
        text_b = await menu_text(workspace_b)
    finally:
        await pool.execute("UPDATE workspaces SET plan_limit = NULL WHERE id = $1", WS_B)

    assert after == before
    assert "Плана на эту неделю пока нет" in after and "🎁 Пробный доступ\n" in after
    assert after.endswith("🎯 Ниша: не задана — укажите в ⚙️ Настройках")
    # B's own menu does show them: the data is there, just not A's.
    assert "«Тема B»" in text_b and "🎁 Доступ\n" in text_b and "юристы B" in text_b
