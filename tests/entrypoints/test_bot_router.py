"""The bot's per-update Пространство (ADR-0015): updates fed through aiogram's real routing, and
the text-answer path. Only the Telegram network edge is faked - our own `BotClient` records
what the bot would send."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast

import pytest
from aiogram import Bot, Dispatcher
from aiogram.methods import AnswerCallbackQuery
from aiogram.types import (
    CallbackQuery,
    Chat,
    Document,
    InaccessibleMessage,
    Message,
    Update,
    User,
)

from content_zavod.config import load_settings
from content_zavod.entrypoints.bot import _build_router, answer_text
from content_zavod.settings import OnboardingState, SettingsService
from content_zavod.telegram import (
    ArticleId,
    CommentGatedRegeneration,
    PlanReview,
    TelegramCommentPrompt,
    TelegramGateway,
)
from content_zavod.telegram.input_prompt import InputPrompt
from content_zavod.telegram.main_menu import MainMenu
from content_zavod.telegram.onboarding import Onboarding
from content_zavod.telegram.settings_screen import SETTING_INPUT_KIND, SettingsScreen
from content_zavod.telegram.start_guard import START_INTERVAL_SECONDS, StartGuard
from content_zavod.telegram.style_samples import StyleSamples
from content_zavod.telegram.texts import (
    ADMIN_HINT,
    ADMIN_LIMIT_DEFAULT,
    ADMIN_LIMIT_SET,
    GROUP_START_TEXT,
    NO_WORKSPACE_TEXT,
    SIGNUP_CLOSED_TEXT,
    STALE_BUTTON,
    UNKNOWN_MESSAGE_TEXT,
)
from content_zavod.workspaces import Usage, Workspace, WorkspaceId
from content_zavod.workspaces.admin import WorkspaceOverview
from tests.telegram.fakes import FakeJobs, FakeLimits, FakePendingInputs, RecordingBot
from tests.tenancy import WS_A, WS_B

ADMIN = 7001
USER = 7002
GROUP = -100777


class FakeWorkspaces:
    """`Workspaces` in memory: one per owner, ids from 1."""

    def __init__(self) -> None:
        self.by_owner: dict[int, Workspace] = {}

    async def get_or_create(
        self, owner_telegram_id: int, *, timezone: str, owner_username: str | None = None
    ) -> Workspace:
        if owner_telegram_id not in self.by_owner:
            self.by_owner[owner_telegram_id] = Workspace(
                WorkspaceId(len(self.by_owner) + 1),
                owner_telegram_id,
                owner_username,
                timezone,
                None,
                None,
            )
        return self.by_owner[owner_telegram_id]

    async def get_by_owner(self, owner_telegram_id: int) -> Workspace | None:
        return self.by_owner.get(owner_telegram_id)

    async def count_created_last_day(self) -> int:
        return len(self.by_owner)  # all of them created today

    async def get(self, workspace_id: WorkspaceId) -> Workspace | None:
        return next((ws for ws in self.by_owner.values() if ws.id == workspace_id), None)

    async def mark_blocked(self, workspace_id: WorkspaceId) -> None: ...


class FakeStore:
    """Настройки of several Пространства side by side."""

    def __init__(self) -> None:
        self.values: dict[tuple[int, str], str] = {}

    async def get(self, workspace_id: int, key: str) -> str | None:
        return self.values.get((workspace_id, key))

    async def set(self, workspace_id: int, key: str, value: str) -> None:
        self.values[workspace_id, key] = value

    async def set_if_changed(self, workspace_id: int, key: str, value: str) -> bool:
        changed = self.values.get((workspace_id, key)) != value
        self.values[workspace_id, key] = value
        return changed


class FakeSchedule:
    async def get(self, workspace_id: int) -> None:
        return None

    async def set(self, workspace_id: int, day_of_week: str, hour: int, minute: int) -> None: ...


class Unused:
    """A collaborator these updates must never reach."""

    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"unexpected call: {name}")


class FakeAdmin:
    def __init__(self) -> None:
        self.limits_set: list[tuple[int, int, int, int]] = []

    async def overview(self, *, limit: int) -> tuple[list[WorkspaceOverview], int]:
        usage = Usage(plans_used=1, plan_limit=1, articles_used=6, article_limit=8)
        workspace = WorkspaceOverview(
            WorkspaceId(12), "anna", datetime(2026, 10, 12, tzinfo=UTC), None, usage, False
        )
        return [workspace], 1

    async def set_limits(
        self, admin_telegram_id: int, workspace_id: int, plans: int | None, articles: int | None
    ) -> bool:
        self.limits_set.append((admin_telegram_id, workspace_id, plans, articles))
        return True


class NoSpend:
    async def usage_for(self, workspace_id: int, since: datetime) -> list:
        return []


class NoPlanOps:
    async def delete_item(self, *args: object) -> None:
        raise AssertionError("unexpected call")

    async def regenerate_item(self, *args: object) -> None:
        raise AssertionError("unexpected call")

    async def approve_all(self, *args: object) -> None:
        raise AssertionError("unexpected call")


class Bot_:
    """Everything the router is built from; `bot` records what the bot sends."""

    def __init__(self, **env_overrides: str) -> None:
        self.bot = RecordingBot()
        self.clock = 1000.0
        self.workspaces = FakeWorkspaces()
        self.store = FakeStore()
        self.pending = FakePendingInputs()
        gateway = TelegramGateway(self.bot)
        prompts = InputPrompt(self.bot, self.pending)
        settings = SettingsService(self.store)
        menu = MainMenu(
            cast(Any, Unused()),
            cast(Any, Unused()),
            FakeSchedule(),
            self.bot,
            gateway,
            prompts,
            settings=settings,
            onboarding=OnboardingState(self.store),
            limits=FakeLimits(),
        )
        self.admin = FakeAdmin()
        self.flows = dict(
            plan_review=PlanReview(NoPlanOps(), TelegramCommentPrompt(self.bot), self.pending),
            article_regeneration=CommentGatedRegeneration[ArticleId](
                cast(Any, Unused()),
                TelegramCommentPrompt(self.bot),
                self.pending,
                kind="article_comment",
            ),
            settings_screen=SettingsScreen(
                settings, FakeSchedule(), cast(Any, Unused()), self.bot, prompts
            ),
            main_menu=menu,
            onboarding=Onboarding(
                OnboardingState(self.store), settings, prompts, self.bot, menu, FakeSchedule()
            ),
            prompts=prompts,
            gateway=gateway,
            style_samples=StyleSamples(
                FakeJobs(),
                settings,
                self.pending,
                prompts,
                self.bot,
                self.download,
                OnboardingState(self.store),
                FakeLimits(),
            ),
        )
        self.files: dict[str, bytes] = {}
        env = {
            "TELEGRAM_BOT_TOKEN": "123456:test",
            "POSTGRES_DSN": "postgresql://localhost/x",
            "YANDEX_FOLDER_ID": "f",
            "YANDEX_API_KEY": "k",
            "GLOBAL_ADMIN_TELEGRAM_IDS": str(ADMIN),
            **env_overrides,
        }
        self.dispatcher = Dispatcher()
        self.dispatcher.include_router(
            _build_router(
                self.workspaces,
                cast(Any, Unused()),
                cast(Any, Unused()),
                gateway,
                self.bot,
                self.flows["plan_review"],
                self.flows["article_regeneration"],
                self.flows["settings_screen"],
                menu,
                prompts,
                cast(Any, Unused()),
                load_settings(env),
                self.flows["onboarding"],
                cast(Any, Unused()),
                bot_username="zavod_bot",
                start_guard=StartGuard(clock=lambda: self.clock),
                limits=cast(Any, FakeLimits()),
                admin=self.admin,
                spend=NoSpend(),
                style_samples=self.flows["style_samples"],
            )
        )
        self.aiogram_bot = Bot(token="123456:test")
        self._update_id = 0
        self.answers: list[tuple[str | None, bool | None]] = []

    async def download(self, file_id: str) -> bytes:
        return self.files[file_id]

    async def message(
        self,
        text: str | None,
        *,
        user: int,
        chat: int | None = None,
        document: Document | None = None,
    ) -> None:
        self._update_id += 1
        chat_id = user if chat is None else chat
        message = Message(
            message_id=self._update_id,
            date=datetime.now(UTC),
            chat=Chat(id=chat_id, type="private" if chat_id == user else "supergroup"),
            from_user=User(id=user, is_bot=False, first_name="U"),
            text=text,
            document=document,
        )
        await self.dispatcher.feed_update(
            self.aiogram_bot, Update(update_id=self._update_id, message=message)
        )

    async def press(
        self,
        data: str,
        *,
        user: int,
        chat: int | None = None,
        inaccessible: bool = False,
        no_message: bool = False,
    ) -> list[tuple[str | None, bool | None]]:
        """A button press; returns what the bot answered the callback with."""
        self._update_id += 1
        chat_id = user if chat is None else chat
        chat_ = Chat(id=chat_id, type="private" if chat_id == user else "supergroup")
        message: Message | InaccessibleMessage = (
            InaccessibleMessage(chat=chat_, message_id=0, date=0)
            if inaccessible
            else Message(message_id=5, date=datetime.now(UTC), chat=chat_, text="…")
        )
        query = CallbackQuery(
            id=str(self._update_id),
            from_user=User(id=user, is_bot=False, first_name="U"),
            chat_instance="c",
            message=None if no_message else message,
            inline_message_id="inline" if no_message else None,
            data=data,
        )
        self.answers.clear()
        await self.dispatcher.feed_update(
            self.aiogram_bot, Update(update_id=self._update_id, callback_query=query)
        )
        return list(self.answers)

    def texts(self, chat: int) -> list[str]:
        return [text for chat_id, text, _, _ in self.bot.sent if chat_id == chat]


def _patched(tg: Bot_, monkeypatch: pytest.MonkeyPatch) -> Bot_:
    async def telegram_api(self: Bot, method: Any, request_timeout: int | None = None) -> Any:
        if isinstance(method, AnswerCallbackQuery):
            tg.answers.append((method.text, method.show_alert))
        return True

    monkeypatch.setattr(Bot, "__call__", telegram_api)
    return tg


@pytest.fixture
def bot(monkeypatch: pytest.MonkeyPatch) -> Bot_:
    return _patched(Bot_(), monkeypatch)


async def test_admin_from_anyone_else_is_an_unknown_command(bot: Bot_) -> None:
    """ADR-0015: /admin is not even hinted at for a non-admin - same as any unknown command."""
    await bot.message("/start", user=USER)
    bot.bot.sent.clear()

    await bot.message("/admin", user=USER)
    await bot.message("/whatever", user=USER)

    assert bot.texts(USER) == [UNKNOWN_MESSAGE_TEXT, UNKNOWN_MESSAGE_TEXT]


async def test_admin_from_a_global_admin_is_answered(bot: Bot_) -> None:
    await bot.message("/start", user=ADMIN)
    bot.bot.sent.clear()

    await bot.message("/admin", user=ADMIN)
    await bot.message("/admin limit 12 2 12", user=ADMIN)

    overview, limit_set = bot.texts(ADMIN)
    assert "#12 @anna" in overview and "Планы 1/1 · Статьи 6/8" in overview
    assert "30 дней: расхода нет" in overview
    assert limit_set == ADMIN_LIMIT_SET.format(id=12, plans=2, articles=12)
    assert bot.admin.limits_set == [(ADMIN, 12, 2, 12)]


async def test_admin_limit_default_and_out_of_range_numbers(bot: Bot_) -> None:
    await bot.message("/start", user=ADMIN)
    bot.bot.sent.clear()

    await bot.message("/admin limit 12 default", user=ADMIN)
    for bad in ("12 1000001 1", "9223372036854775808 1 1", "12 1", "12 x 1"):
        await bot.message(f"/admin limit {bad}", user=ADMIN)

    assert bot.texts(ADMIN) == [ADMIN_LIMIT_DEFAULT.format(id=12)] + [ADMIN_HINT] * 4
    assert bot.admin.limits_set == [(ADMIN, 12, None, None)]


async def test_start_creates_one_workspace_per_person_and_opens_the_wizard(bot: Bot_) -> None:
    await bot.message("/start", user=USER)
    await bot.message("/start", user=ADMIN)
    await bot.message("/start", user=USER)

    assert set(bot.workspaces.by_owner) == {USER, ADMIN}
    assert bot.texts(USER)[0].startswith("👋 Здравствуйте! Я помогаю вести блог")


async def test_before_start_a_message_only_asks_for_start(bot: Bot_) -> None:
    await bot.message("/menu", user=USER)
    await bot.message("привет", user=USER)

    assert bot.texts(USER) == [NO_WORKSPACE_TEXT, NO_WORKSPACE_TEXT]
    assert bot.workspaces.by_owner == {}


async def test_in_a_group_only_start_is_answered_with_a_pointer_to_the_private_chat(
    bot: Bot_,
) -> None:
    await bot.message("/start", user=USER, chat=GROUP)
    await bot.message("/menu", user=USER, chat=GROUP)
    await bot.message("привет", user=USER, chat=GROUP)

    ((chat_id, text, markup, _),) = bot.bot.sent
    assert (chat_id, text) == (GROUP, GROUP_START_TEXT)
    assert markup.inline_keyboard[0][0].url == "https://t.me/zavod_bot"
    assert bot.workspaces.by_owner == {}


async def test_a_text_of_a_does_not_close_bs_wait(bot: Bot_) -> None:
    """ADR-0015: B's open question (here a Настройка), under the very same chat and user ids,
    is invisible to A's text - nothing of B is written, its wait stays, A gets the menu hint."""
    workspace_a = Workspace(WS_A, USER, None, "Europe/Moscow", None, None)
    workspace_b = Workspace(WS_B, USER, None, "Europe/Moscow", None, None)
    await bot.flows["settings_screen"].ask(workspace_b, USER, USER, "niche", screen_message_id=5)

    await answer_text(workspace_a, USER, USER, "юристы", **bot.flows)

    assert bot.pending.by_workspace[WS_B][(USER, USER)].kind == SETTING_INPUT_KIND
    assert bot.store.values == {}
    assert bot.texts(USER)[-1] == UNKNOWN_MESSAGE_TEXT

    await answer_text(workspace_b, USER, USER, "юристы", **bot.flows)

    assert bot.store.values == {(WS_B, "niche"): "юристы"}


async def test_a_button_in_a_group_acts_on_nothing(bot: Bot_) -> None:
    """The old team chat's buttons: the presser's own Пространство is not the group's."""
    await bot.message("/start", user=USER)
    bot.bot.sent.clear()

    assert await bot.press("mn:", user=USER, chat=GROUP) == [(STALE_BUTTON, True)]
    assert bot.bot.sent == [] and bot.bot.edited == []


async def test_a_button_before_start_asks_for_start(bot: Bot_) -> None:
    assert await bot.press("mn:", user=USER) == [(NO_WORKSPACE_TEXT, True)]
    assert bot.workspaces.by_owner == {}


async def test_a_button_on_a_message_telegram_no_longer_shows_is_still_answered(
    bot: Bot_,
) -> None:
    """No spinner left on the button, even when there is no message to act on."""
    await bot.message("/start", user=USER)

    assert await bot.press("mn:", user=USER, inaccessible=True) == [(None, None)]
    assert await bot.press("mn:", user=USER, no_message=True) == [(None, None)]


async def test_an_old_buttons_unknown_code_is_answered_silently(bot: Bot_) -> None:
    await bot.message("/start", user=USER)

    assert await bot.press("ra:7", user=USER) == [(None, None)]  # «Запросить доступ», #118


async def test_through_the_router_a_text_of_a_does_not_close_bs_wait(bot: Bot_) -> None:
    """A and B are two people: A's text reaches only A's Пространство."""
    await bot.message("/start", user=USER)
    await bot.message("/start", user=ADMIN)
    workspace_b = bot.workspaces.by_owner[ADMIN]
    await bot.flows["settings_screen"].ask(workspace_b, ADMIN, ADMIN, "niche", screen_message_id=5)

    await bot.message("юристы", user=USER)

    assert bot.pending.by_workspace[workspace_b.id][(ADMIN, ADMIN)].kind == SETTING_INPUT_KIND
    assert (workspace_b.id, "niche") not in bot.store.values


async def test_frequent_start_creates_one_workspace_and_is_answered_once_per_interval(
    bot: Bot_,
) -> None:
    """ADR-0015: /start from one id at most once per 3 seconds; the rest are silently dropped."""
    await bot.message("/start", user=USER)
    answered = len(bot.texts(USER))
    for _ in range(5):
        bot.clock += 0.5
        await bot.message("/start", user=USER)

    assert answered > 0
    assert len(bot.texts(USER)) == answered
    assert list(bot.workspaces.by_owner) == [USER]

    bot.clock += START_INTERVAL_SECONDS
    await bot.message("/start", user=USER)

    assert len(bot.texts(USER)) > answered
    assert list(bot.workspaces.by_owner) == [USER]


async def test_past_the_signup_limit_no_workspace_is_made_and_admins_hear_once_a_day(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bot = _patched(Bot_(SIGNUP_DAILY_LIMIT="1"), monkeypatch)
    newcomer, another = 7003, 7004
    await bot.message("/start", user=USER)

    await bot.message("/start", user=newcomer)
    await bot.message("/start", user=another)

    assert list(bot.workspaces.by_owner) == [USER]
    assert bot.texts(newcomer) == [SIGNUP_CLOSED_TEXT]
    assert bot.texts(another) == [SIGNUP_CLOSED_TEXT]
    (alert,) = bot.texts(ADMIN)
    assert "Новых Пространств за сутки: 1" in alert

    # An existing Пространство is served as usual.
    bot.clock += START_INTERVAL_SECONDS
    bot.bot.sent.clear()
    await bot.message("/start", user=USER)
    await bot.message("/menu", user=USER)
    assert SIGNUP_CLOSED_TEXT not in bot.texts(USER)
    assert len(bot.texts(USER)) >= 2

    # A day later the admins are told again, if it is still closed.
    bot.clock += 24 * 60 * 60
    await bot.message("/start", user=newcomer)
    assert len(bot.texts(ADMIN)) == 1


async def test_a_global_admin_gets_a_workspace_past_the_signup_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bot = _patched(Bot_(SIGNUP_DAILY_LIMIT="0"), monkeypatch)

    await bot.message("/start", user=ADMIN)

    assert list(bot.workspaces.by_owner) == [ADMIN]
    assert SIGNUP_CLOSED_TEXT not in bot.texts(ADMIN)


async def test_an_alert_no_admin_received_is_tried_again_on_the_next_refusal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bot = _patched(Bot_(SIGNUP_DAILY_LIMIT="0"), monkeypatch)
    send = bot.bot.send_message

    async def admin_unreachable(chat_id: int, text: str, reply_markup: object = None) -> int:
        if chat_id == ADMIN:
            raise RuntimeError("Forbidden: bot can't initiate conversation with a user")
        return await send(chat_id, text, reply_markup)

    monkeypatch.setattr(bot.bot, "send_message", admin_unreachable)
    await bot.message("/start", user=USER)
    monkeypatch.setattr(bot.bot, "send_message", send)
    await bot.message("/start", user=7003)
    await bot.message("/start", user=7004)

    assert len(bot.texts(ADMIN)) == 1


async def test_start_in_a_group_does_not_use_up_the_private_one(bot: Bot_) -> None:
    await bot.message("/start", user=USER, chat=GROUP)
    await bot.message("/start", user=USER)

    assert list(bot.workspaces.by_owner) == [USER]
    assert bot.texts(USER)


def test_start_guard_forgets_ids_past_the_interval_once_it_holds_many() -> None:
    now = 0.0
    guard = StartGuard(clock=lambda: now)
    for owner in range(10_000):
        assert guard.admit(owner)

    now = START_INTERVAL_SECONDS
    assert guard.admit(-1)

    assert guard._last_start == {-1: START_INTERVAL_SECONDS}


async def test_texts_and_files_reach_the_style_samples_of_the_senders_workspace(
    bot: Bot_,
) -> None:
    """#134: while Образцы are collected, a text or a .txt file is an Образец, not an answer."""
    await bot.message("/start", user=USER)
    await bot.press("ya:a", user=USER)
    bot.files["f1"] = "Мой текст из файла.".encode()

    await bot.message("Первая часть статьи.", user=USER)
    await bot.message(
        None,
        user=USER,
        document=Document(file_id="f1", file_unique_id="u1", file_name="a.txt", file_size=40),
    )

    assert "Получено: 39 знаков текста" in bot.bot.edited[-1][2]
    assert UNKNOWN_MESSAGE_TEXT not in bot.texts(USER)
