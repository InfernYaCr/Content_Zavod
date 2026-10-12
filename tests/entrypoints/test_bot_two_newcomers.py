"""The done criterion of #118 PR 4, end to end on a real database: two people press /start at
the same time, each goes through the Онбординг and «🚀 Запустить», and each gets their own
Plan in their own private chat - nothing of the other's. Telegram is the only fake: updates go
through aiogram's real routing, and our `BotClient` records what would be sent."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import asyncpg
import pytest
from aiogram import Bot, Dispatcher
from aiogram.types import CallbackQuery, Chat, Message, Update, User

from content_zavod.config import load_settings
from content_zavod.domain import Article, Plan
from content_zavod.entrypoints.bot import _build_router, _make_notification_handler
from content_zavod.job_queue import JobQueue
from content_zavod.owner_settings import BotStateStore, WorkspaceSettingsStore
from content_zavod.scheduling import ScheduleSettings
from content_zavod.settings import OnboardingState, SettingsService
from content_zavod.telegram import (
    ArticleId,
    CommentGatedRegeneration,
    PlanReview,
    TelegramCommentPrompt,
    TelegramGateway,
)
from content_zavod.telegram.asset_photos import AssetPhotos
from content_zavod.telegram.guide import Guide
from content_zavod.telegram.input_prompt import InputPrompt
from content_zavod.telegram.main_menu import MainMenu
from content_zavod.telegram.onboarding import Onboarding
from content_zavod.telegram.pending_inputs import PendingInputs
from content_zavod.telegram.settings_screen import SettingsScreen
from content_zavod.workspaces import Workspaces
from tests.telegram.fakes import RecordingBot
from tests.tenancy import WS_A, WS_B

ANNA = 8101
BORIS = 8102


@pytest.fixture
async def clean(pool: asyncpg.Pool) -> None:
    await pool.execute(
        "TRUNCATE TABLE jobs, article_versions, articles, plan_items, plans, generation_steps, "
        "pending_inputs, workspace_settings, schedule_settings, limit_charges CASCADE"
    )
    await pool.execute("DELETE FROM workspaces WHERE id NOT IN ($1, $2)", WS_A, WS_B)


class Telegram:
    def __init__(self, pool: asyncpg.Pool, monkeypatch: pytest.MonkeyPatch) -> None:
        async def answered(self: Bot, method: Any, request_timeout: int | None = None) -> Any:
            return True  # callback.answer()

        monkeypatch.setattr(Bot, "__call__", answered)
        self.bot = RecordingBot()
        self.queue = JobQueue(pool)
        self.workspaces = Workspaces(pool)
        self.plan = Plan(pool, self.queue)
        article = Article(pool, self.queue)
        store = WorkspaceSettingsStore(pool)
        settings = SettingsService(store)
        schedule = ScheduleSettings(pool)
        pending = PendingInputs(pool)
        self.gateway = TelegramGateway(self.bot)
        prompts = InputPrompt(self.bot, pending)
        menu = MainMenu(
            self.plan,
            self.queue,
            schedule,
            self.bot,
            self.gateway,
            prompts,
            settings=settings,
            onboarding=OnboardingState(store),
        )
        self.settings = settings
        config = load_settings(
            {
                "TELEGRAM_BOT_TOKEN": "123456:test",
                "POSTGRES_DSN": "postgresql://localhost/x",
                "YANDEX_FOLDER_ID": "f",
                "YANDEX_API_KEY": "k",
            }
        )
        self.dispatcher = Dispatcher()
        self.dispatcher.include_router(
            _build_router(
                self.workspaces,
                self.plan,
                article,
                self.gateway,
                self.bot,
                PlanReview(self.plan, TelegramCommentPrompt(self.bot), pending),
                CommentGatedRegeneration[ArticleId](
                    article.request_regeneration,
                    TelegramCommentPrompt(self.bot, action="regenerate_article"),
                    pending,
                    kind="article_comment",
                ),
                SettingsScreen(settings, schedule, self.workspaces, self.bot, prompts),
                menu,
                prompts,
                self.queue,
                config,
                Onboarding(OnboardingState(store), settings, prompts, self.bot, menu, schedule),
                Guide(AssetPhotos(self.bot, BotStateStore(pool)), self.bot),
            )
        )
        self.notify = _make_notification_handler(self.plan, article, self.gateway, self.workspaces)
        self.aiogram_bot = Bot(token="123456:test")
        self._update_id = 0

    async def _feed(self, **update: Any) -> None:
        self._update_id += 1
        await self.dispatcher.feed_update(
            self.aiogram_bot, Update(update_id=self._update_id, **update)
        )

    async def text(self, user: int, text: str) -> None:
        await self._feed(message=self._message(user, self._update_id + 1, text))

    async def press(self, user: int, message_id: int, data: str) -> None:
        await self._feed(
            callback_query=CallbackQuery(
                id=str(self._update_id),
                from_user=User(id=user, is_bot=False, first_name="U"),
                chat_instance="c",
                message=self._message(user, message_id, "…"),
                data=data,
            )
        )

    @staticmethod
    def _message(user: int, message_id: int, text: str) -> Message:
        return Message(
            message_id=message_id,
            date=datetime.now(UTC),
            chat=Chat(id=user, type="private"),
            from_user=User(id=user, is_bot=False, first_name="U"),
            text=text,
        )

    def last_to(self, user: int) -> tuple[int, str]:
        """(message id, text) of the newest message sent to `user`'s chat."""
        message_id = max(
            index for index, (chat, *_rest) in enumerate(self.bot.sent) if chat == user
        )
        return 100 + message_id, self.bot.sent[message_id][1]

    def texts_to(self, user: int) -> str:
        return "\n".join(text for chat, text, *_ in self.bot.sent if chat == user)

    async def work_and_notify(self, topic_by_niche: dict[str, str]) -> None:
        """The worker's part, faked at the queue: each `generate_plan` Job gets one Тема named
        after its own Пространство's Ниша; then every notification is delivered."""
        while (claimed := await self.queue.claim_next()) is not None:
            niche = (await self.settings.read(claimed.workspace_id)).niche
            output = {
                "week_label": claimed.payload["week_label"],
                "topics": [{"title": topic_by_niche[niche], "summary": "", "keywords": []}],
            }
            await self.queue.complete(claimed.id, output, claimed.lease_token)
        while (notification := await self.queue.claim_notification()) is not None:
            await self.notify(notification.result)
            await self.queue.mark_notified(notification.result.job_id)


async def test_two_newcomers_each_get_their_own_plan_in_their_own_chat(
    pool: asyncpg.Pool, clean: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    tg = Telegram(pool, monkeypatch)

    # /start: one Пространство each, and the wizard's intro.
    await tg.text(ANNA, "/start")
    await tg.text(BORIS, "/start")
    anna, boris = await tg.workspaces.get_by_owner(ANNA), await tg.workspaces.get_by_owner(BORIS)
    assert anna is not None and boris is not None and anna.id != boris.id
    for user in (ANNA, BORIS):
        intro, text = tg.last_to(user)
        assert text.startswith("👋 Здравствуйте!")
        await tg.press(user, intro, "ob:start")  # «▶ Начать настройку»

    # Each answers the Ниша question - interleaved, same moment.
    await tg.text(ANNA, "кофейни")
    await tg.text(BORIS, "юристы")
    assert (await tg.settings.read(anna.id)).niche == "кофейни"
    assert (await tg.settings.read(boris.id)).niche == "юристы"

    # «🚀 Запустить» from the review card - straight there, the other steps are optional.
    for user in (ANNA, BORIS):
        await tg.press(user, 1, "ob:review")
        review, text = tg.last_to(user)
        assert text.startswith("🔎 Проверьте вводные")
        await tg.press(user, review, "og:")

    await tg.work_and_notify({"кофейни": "Кофе с собой", "юристы": "Договор аренды"})

    assert "Кофе с собой" in tg.texts_to(ANNA)
    assert "Договор аренды" in tg.texts_to(BORIS)
    assert "Договор аренды" not in tg.texts_to(ANNA) and "юристы" not in tg.texts_to(ANNA)
    assert "Кофе с собой" not in tg.texts_to(BORIS) and "кофейни" not in tg.texts_to(BORIS)
    assert {chat for chat, *_ in tg.bot.sent} == {ANNA, BORIS}
