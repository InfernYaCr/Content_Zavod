"""bot_main: the process that talks to Telegram (ADR-0004).

Registers PlanReview / CommentGatedRegeneration (#4/#9) and the manual
/topic command (#10) behind the Membership allowlist (#8) - an
unregistered telegram_id gets an explicit refusal, never silence. Runs
`run_notifications` (#2) to deliver finished Job results back to the team
chat, and `schedule_weekly_plan_trigger` (#7) via APScheduler in the same
process. Never runs a Job Handler itself (see entrypoints/worker.py) - it
only enqueues (through Plan/Article) and renders results.
"""

from __future__ import annotations

import asyncio
import base64
import logging

import asyncpg
from aiogram import Bot, Dispatcher, Router
from aiogram.filters import Command
from aiogram.types import BufferedInputFile, CallbackQuery, InlineKeyboardMarkup, Message
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from ..access import Membership, Role
from ..config import Settings, load_settings
from ..domain import Article, ArticleId, GeneratedVersion, Plan, PlanItemId, TopicDraft
from ..job_queue import JobQueue, JobResult, run_notifications
from ..scheduling import schedule_weekly_plan_trigger
from ..telegram import (
    PlanReview,
    TelegramCommentPrompt,
    TelegramGateway,
    decode_callback_data,
    handle_topic_command,
)
from ._process import register_shutdown

logger = logging.getLogger(__name__)

_ACCESS_DENIED_TEXT = "Доступ запрещён. Обратитесь к владельцу бота, чтобы получить роль."


class _AiogramBotClient:
    """Adapts `aiogram.Bot` to the narrow `BotClient` protocol the telegram layer depends on."""

    def __init__(self, bot: Bot) -> None:
        self._bot = bot

    async def send_message(
        self, chat_id: int, text: str, reply_markup: InlineKeyboardMarkup | None = None
    ) -> None:
        await self._bot.send_message(chat_id, text, reply_markup=reply_markup)

    async def send_document(
        self, chat_id: int, document: BufferedInputFile, caption: str | None = None
    ) -> None:
        await self._bot.send_document(chat_id, document, caption=caption)


async def _role_for(membership: Membership, gateway: TelegramGateway, chat_id: int, telegram_id: int) -> Role | None:
    role = await membership.role_for(telegram_id)
    if role is None:
        await gateway.send_error(chat_id, _ACCESS_DENIED_TEXT)
    return role


def _build_router(
    membership: Membership,
    plan: Plan,
    gateway: TelegramGateway,
    plan_review: PlanReview,
    settings: Settings,
) -> Router:
    router = Router()

    @router.message(Command("topic"))
    async def on_topic(message: Message) -> None:
        if message.from_user is None:
            return
        if await _role_for(membership, gateway, message.chat.id, message.from_user.id) is None:
            return
        parts = (message.text or "").split(maxsplit=1)
        text = parts[1] if len(parts) > 1 else ""
        await handle_topic_command(plan, gateway, message.chat.id, text, tz=settings.timezone)

    @router.callback_query()
    async def on_callback(callback: CallbackQuery) -> None:
        await callback.answer()
        if callback.from_user is None or callback.message is None:
            return
        chat_id = callback.message.chat.id
        if await _role_for(membership, gateway, chat_id, callback.from_user.id) is None:
            return
        try:
            action, id_ = decode_callback_data(callback.data or "")
        except ValueError:
            return
        if action not in ("delete", "regenerate", "approve_all"):
            # "approve" (accepting a generated Статья) has no domain-layer glue yet - see #13 gap.
            await gateway.send_error(chat_id, "Приём готовых Статей пока не реализован.")
            return
        await plan_review.handle_action(chat_id, callback.from_user.id, PlanItemId(id_), action)

    @router.message()
    async def on_message(message: Message) -> None:
        if message.from_user is None:
            return
        if await _role_for(membership, gateway, message.chat.id, message.from_user.id) is None:
            return
        await plan_review.handle_comment_reply(message.chat.id, message.from_user.id, message.text or "")

    return router


def _make_notification_handler(
    plan: Plan, article: Article, gateway: TelegramGateway, notify_chat_id: int
):
    async def handle(result: JobResult) -> None:
        if result.status == "failed":
            await gateway.send_error(
                notify_chat_id, f"Задача {result.job_type} завершилась ошибкой: {result.error}"
            )
            return

        output = result.output or {}
        if result.job_type == "generate_plan":
            topics = [
                TopicDraft(title=t["title"], summary=t.get("summary", ""), keywords=t.get("keywords", []))
                for t in output["topics"]
            ]
            plan_id = await plan.add_topics(output["week_label"], topics)
            view = await plan.get(plan_id)
            await gateway.send_plan(notify_chat_id, view)
        elif result.job_type == "regenerate_topic":
            plan_item_id = PlanItemId(output["plan_item_id"])
            await plan.apply_regeneration(
                plan_item_id,
                TopicDraft(title=output["title"], summary=output["summary"], keywords=output["keywords"]),
            )
            await gateway.send_notice(notify_chat_id, f"Тема обновлена: {output['title']}")
        elif result.job_type in ("generate_article", "regenerate_article"):
            article_id = ArticleId(output["article_id"])
            await article.record_version(
                article_id,
                GeneratedVersion(
                    content=output["content"],
                    prompt=output["prompt"],
                    model=output["model"],
                    tokens=output["tokens"],
                    cost=output["cost"],
                ),
            )
            view = await article.get(article_id)
            await gateway.send_article_ready(notify_chat_id, view)
        elif result.job_type == "generate_cover":
            plan_item_id = PlanItemId(output["plan_item_id"])
            image = base64.b64decode(output["image"])
            await plan.apply_cover(plan_item_id, image, output["mime_type"])
            await gateway.send_notice(notify_chat_id, "Обложка готова.")
        else:
            logger.warning("No notification renderer for job_type=%r", result.job_type)

    return handle


async def main(settings: Settings | None = None) -> None:
    logging.basicConfig(level=logging.INFO)
    settings = settings or load_settings()

    pool = await asyncpg.create_pool(dsn=settings.postgres_dsn)
    assert pool is not None
    try:
        queue = JobQueue(pool)
        await queue.ensure_schema()
        membership = Membership(pool)
        await membership.ensure_schema()
        plan = Plan(pool, queue)
        await plan.ensure_schema()
        article = Article(pool, queue)
        await article.ensure_schema()

        bot = Bot(token=settings.telegram_bot_token)
        bot_client = _AiogramBotClient(bot)
        gateway = TelegramGateway(bot_client)
        comment_prompt = TelegramCommentPrompt(bot_client)
        plan_review = PlanReview(plan, comment_prompt)

        dispatcher = Dispatcher()
        dispatcher.include_router(_build_router(membership, plan, gateway, plan_review, settings))

        scheduler = AsyncIOScheduler()
        schedule_weekly_plan_trigger(scheduler, plan, tz=settings.timezone)
        scheduler.start()

        stop = asyncio.Event()
        register_shutdown(stop)

        notify_handler = _make_notification_handler(plan, article, gateway, settings.telegram_notify_chat_id)
        polling_task = asyncio.create_task(dispatcher.start_polling(bot, handle_signals=False))
        notifications_task = asyncio.create_task(run_notifications(queue, notify_handler, stop=stop))

        logger.info("bot started")
        try:
            await stop.wait()
        finally:
            await dispatcher.stop_polling()
            await polling_task
            await notifications_task
            scheduler.shutdown(wait=False)
            await bot.session.close()
    finally:
        await pool.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
