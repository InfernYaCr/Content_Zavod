"""The bot's per-update Пространство (ADR-0015): updates fed through aiogram's real routing, and
the text-answer path. Only the Telegram network edge is faked - our own `BotClient` records
what the bot would send."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast

import pytest
from aiogram import Bot, Dispatcher
from aiogram.types import Chat, Message, Update, User

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
from content_zavod.telegram.texts import (
    ADMIN_PENDING_TEXT,
    GROUP_START_TEXT,
    NO_WORKSPACE_TEXT,
    UNKNOWN_MESSAGE_TEXT,
)
from content_zavod.workspaces import Workspace, WorkspaceId
from tests.telegram.fakes import FakePendingInputs, RecordingBot
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


class NoPlanOps:
    async def delete_item(self, *args: object) -> None:
        raise AssertionError("unexpected call")

    async def regenerate_item(self, *args: object) -> None:
        raise AssertionError("unexpected call")

    async def approve_all(self, *args: object) -> None:
        raise AssertionError("unexpected call")


class Bot_:
    """Everything the router is built from; `bot` records what the bot sends."""

    def __init__(self) -> None:
        self.bot = RecordingBot()
        self.workspaces = FakeWorkspaces()
        self.store = FakeStore()
        self.pending = FakePendingInputs()
        gateway = TelegramGateway(self.bot)
        prompts = InputPrompt(self.bot, self.pending)
        settings = SettingsService(self.store)
        menu = MainMenu(
            cast(Any, Unused()), cast(Any, Unused()), FakeSchedule(), self.bot, gateway, prompts
        )
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
        )
        env = {
            "TELEGRAM_BOT_TOKEN": "123456:test",
            "POSTGRES_DSN": "postgresql://localhost/x",
            "YANDEX_FOLDER_ID": "f",
            "YANDEX_API_KEY": "k",
            "GLOBAL_ADMIN_TELEGRAM_IDS": str(ADMIN),
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
            )
        )
        self.aiogram_bot = Bot(token="123456:test")
        self._update_id = 0

    async def message(self, text: str, *, user: int, chat: int | None = None) -> None:
        self._update_id += 1
        chat_id = user if chat is None else chat
        message = Message(
            message_id=self._update_id,
            date=datetime.now(UTC),
            chat=Chat(id=chat_id, type="private" if chat_id == user else "supergroup"),
            from_user=User(id=user, is_bot=False, first_name="U"),
            text=text,
        )
        await self.dispatcher.feed_update(
            self.aiogram_bot, Update(update_id=self._update_id, message=message)
        )

    def texts(self, chat: int) -> list[str]:
        return [text for chat_id, text, _, _ in self.bot.sent if chat_id == chat]


@pytest.fixture
def bot() -> Bot_:
    return Bot_()


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

    assert bot.texts(ADMIN) == [ADMIN_PENDING_TEXT]


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
