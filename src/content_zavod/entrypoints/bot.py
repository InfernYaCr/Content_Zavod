"""bot_main: the process that talks to Telegram (ADR-0004).

Registers PlanReview / CommentGatedRegeneration (#4/#9), the manual /topic
command (#10), /generate_plan, member/access management, and schedule
management behind the Membership allowlist (#8) - an unregistered
telegram_id gets a "Запросить доступ" prompt from /start rather than silent
refusal everywhere else. Runs `run_notifications` (#2) to deliver finished
Job results back to the team chat, and `schedule_weekly_plan_trigger` (#7)
via APScheduler in the same process. Never runs a Job Handler itself (see
entrypoints/worker.py) - it only enqueues (through Plan/Article) and renders
results.
"""

from __future__ import annotations

import asyncio
import base64
import logging
from collections.abc import Awaitable, Callable
from functools import wraps

import asyncpg
from aiogram import Bot, Dispatcher, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject
from aiogram.types import (
    BotCommand,
    BotCommandScopeChat,
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardMarkup,
    Message,
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from ..access import COMMAND_ROLE, JoinRequests, Membership, Role, require_role
from ..config import Settings, load_settings
from ..domain import (
    Article,
    GeneratedVersion,
    Plan,
    PlanItemId,
    TopicDraft,
)
from ..job_queue import JobQueue, JobResult, run_notifications
from ..owner_settings import OwnerSettingsStore
from ..scheduling import ScheduleSettings, schedule_weekly_plan_trigger
from ..settings import SettingsService
from ..telegram import (
    ArticleId,
    BotClient,
    CommentGatedRegeneration,
    JoinRequestFlow,
    PlanReview,
    TelegramCommentPrompt,
    TelegramGateway,
    build_request_access_keyboard,
    handle_directions_command,
    handle_generate_plan_command,
    handle_history_command,
    handle_members_command,
    handle_niche_command,
    handle_persona_command,
    handle_schedule_command,
    handle_set_directions_command,
    handle_set_niche_command,
    handle_set_persona_command,
    handle_set_schedule_command,
    handle_settings_command,
    handle_topic_command,
    render_help_text,
    sync_commands,
    unpack_callback_query,
)
from ..telegram.callback_dispatcher import (
    _ACCESS_DENIED_TEXT,
    _OWNER_ONLY_TEXT,
    CallbackDispatcher,
)
from ._process import register_shutdown

logger = logging.getLogger(__name__)


class _AiogramBotClient:
    """Adapts `aiogram.Bot` to the narrow `BotClient` protocol the telegram layer depends on."""

    def __init__(self, bot: Bot) -> None:
        self._bot = bot

    async def send_message(
        self, chat_id: int, text: str, reply_markup: InlineKeyboardMarkup | None = None
    ) -> int:
        message = await self._bot.send_message(chat_id, text, reply_markup=reply_markup)
        return message.message_id

    async def send_document(
        self, chat_id: int, document: BufferedInputFile, caption: str | None = None
    ) -> None:
        await self._bot.send_document(chat_id, document, caption=caption)

    async def send_photo(
        self, chat_id: int, photo: BufferedInputFile, caption: str | None = None
    ) -> None:
        await self._bot.send_photo(chat_id, photo, caption=caption)

    async def edit_message_text(
        self,
        chat_id: int,
        message_id: int,
        text: str,
        reply_markup: InlineKeyboardMarkup | None = None,
    ) -> None:
        try:
            await self._bot.edit_message_text(
                text, chat_id=chat_id, message_id=message_id, reply_markup=reply_markup
            )
        except TelegramBadRequest as exc:
            if "message is not modified" not in str(exc):
                raise

    async def edit_message_reply_markup(
        self, chat_id: int, message_id: int, reply_markup: InlineKeyboardMarkup | None = None
    ) -> None:
        try:
            await self._bot.edit_message_reply_markup(
                chat_id=chat_id, message_id=message_id, reply_markup=reply_markup
            )
        except TelegramBadRequest as exc:
            if "message is not modified" not in str(exc):
                raise

    async def set_my_commands(
        self, commands: list[BotCommand], *, scope: BotCommandScopeChat
    ) -> None:
        await self._bot.set_my_commands(commands, scope=scope)


def _build_router(
    membership: Membership,
    plan: Plan,
    article: Article,
    gateway: TelegramGateway,
    bot_client: BotClient,
    plan_review: PlanReview,
    article_regeneration: CommentGatedRegeneration[ArticleId],
    join_request_flow: JoinRequestFlow,
    schedule_settings: ScheduleSettings,
    owner_settings_service: SettingsService,
    queue: JobQueue,
    scheduler: AsyncIOScheduler,
    settings: Settings,
) -> Router:
    router = Router()

    def gated(
        required: Role | None,
    ) -> Callable[[Callable[..., Awaitable[None]]], Callable[..., Awaitable[None]]]:
        """Resolve the caller's Role, refuse via `gateway.send_error` on mismatch, otherwise
        run the wrapped handler. `@wraps` keeps the handler's own signature visible to aiogram's
        argument injection, so `message`/`command` still reach it unchanged."""

        def decorator(handler: Callable[..., Awaitable[None]]) -> Callable[..., Awaitable[None]]:
            @wraps(handler)
            async def wrapper(message: Message, **kwargs: object) -> None:
                if message.from_user is None:
                    return
                actual = await membership.role_for(message.from_user.id)
                if not require_role(actual, required):
                    text = _ACCESS_DENIED_TEXT if actual is None else _OWNER_ONLY_TEXT
                    await gateway.send_error(message.chat.id, text)
                    return
                await handler(message, **kwargs)

            return wrapper

        return decorator

    @router.message(Command("start"))
    async def on_start(message: Message) -> None:
        if message.from_user is None:
            return
        chat_id = message.chat.id
        telegram_id = message.from_user.id
        role = await membership.role_for(telegram_id)
        if role is None:
            await gateway.send_message(
                chat_id,
                "Вы не зарегистрированы. Нажмите кнопку, чтобы отправить заявку на доступ владельцу.",
                reply_markup=build_request_access_keyboard(telegram_id),
            )
            return
        await sync_commands(bot_client, telegram_id, role)
        await gateway.send_notice(chat_id, "Добро пожаловать. Список команд: /help")

    @router.message(Command("help"))
    @gated(COMMAND_ROLE["help"])
    async def on_help(message: Message) -> None:
        # gated() already confirmed message.from_user has a registered Role; re-fetch it here
        # because on_help is the one handler that needs the actual value, not just pass/fail.
        role = await membership.role_for(message.from_user.id)
        if role is not None:
            await gateway.send_notice(message.chat.id, render_help_text(role))

    @router.message(Command("topic"))
    @gated(COMMAND_ROLE["topic"])
    async def on_topic(message: Message) -> None:
        parts = (message.text or "").split(maxsplit=1)
        text = parts[1] if len(parts) > 1 else ""
        await handle_topic_command(plan, gateway, message.chat.id, text, tz=settings.timezone)

    @router.message(Command("generate_plan"))
    @gated(COMMAND_ROLE["generate_plan"])
    async def on_generate_plan(message: Message) -> None:
        await handle_generate_plan_command(plan, gateway, message.chat.id, tz=settings.timezone)

    @router.message(Command("history"))
    @gated(COMMAND_ROLE["history"])
    async def on_history(message: Message) -> None:
        await handle_history_command(plan, gateway, message.chat.id)

    @router.message(Command("members"))
    @gated(COMMAND_ROLE["members"])
    async def on_members(message: Message) -> None:
        await handle_members_command(membership, gateway, message.chat.id)

    @router.message(Command("schedule"))
    @gated(COMMAND_ROLE["schedule"])
    async def on_schedule(message: Message) -> None:
        await handle_schedule_command(schedule_settings, gateway, message.chat.id)

    @router.message(Command("set_schedule"))
    @gated(COMMAND_ROLE["set_schedule"])
    async def on_set_schedule(message: Message, command: CommandObject) -> None:
        await handle_set_schedule_command(
            schedule_settings,
            scheduler,
            gateway,
            message.chat.id,
            command.args or "",
            tz=settings.timezone,
        )

    @router.message(Command("niche"))
    @gated(COMMAND_ROLE["niche"])
    async def on_niche(message: Message) -> None:
        await handle_niche_command(owner_settings_service, gateway, message.chat.id)

    @router.message(Command("set_niche"))
    @gated(COMMAND_ROLE["set_niche"])
    async def on_set_niche(message: Message, command: CommandObject) -> None:
        await handle_set_niche_command(
            owner_settings_service, gateway, message.chat.id, command.args or ""
        )

    @router.message(Command("directions"))
    @gated(COMMAND_ROLE["directions"])
    async def on_directions(message: Message) -> None:
        await handle_directions_command(owner_settings_service, gateway, message.chat.id)

    @router.message(Command("set_directions"))
    @gated(COMMAND_ROLE["set_directions"])
    async def on_set_directions(message: Message, command: CommandObject) -> None:
        await handle_set_directions_command(
            owner_settings_service, gateway, message.chat.id, command.args or ""
        )

    @router.message(Command("persona"))
    @gated(COMMAND_ROLE["persona"])
    async def on_persona(message: Message) -> None:
        await handle_persona_command(owner_settings_service, gateway, message.chat.id)

    @router.message(Command("set_persona"))
    @gated(COMMAND_ROLE["set_persona"])
    async def on_set_persona(message: Message, command: CommandObject) -> None:
        await handle_set_persona_command(
            owner_settings_service, gateway, message.chat.id, command.args or ""
        )

    @router.message(Command("settings"))
    @gated(COMMAND_ROLE["settings"])
    async def on_settings(message: Message) -> None:
        await handle_settings_command(owner_settings_service, gateway, message.chat.id)

    dispatcher = CallbackDispatcher(
        membership,
        plan,
        article,
        gateway,
        bot_client,
        plan_review,
        article_regeneration,
        join_request_flow,
        owner_settings_service,
        queue,
    )

    @router.callback_query()
    async def on_callback(callback: CallbackQuery) -> None:
        try:
            callback_input = unpack_callback_query(callback)
        except ValueError:
            await callback.answer()
            return
        if callback_input is None:
            return
        await dispatcher.dispatch(callback_input, callback.answer)

    @router.message()
    async def on_message(message: Message) -> None:
        if message.from_user is None:
            return
        actual = await membership.role_for(message.from_user.id)
        if not require_role(actual, None):
            await gateway.send_error(message.chat.id, _ACCESS_DENIED_TEXT)
            return
        chat_id, user_id, text = message.chat.id, message.from_user.id, message.text or ""
        consumed = await plan_review.handle_comment_reply(chat_id, user_id, text)
        if not consumed:
            await article_regeneration.handle_comment_reply(chat_id, user_id, text)

    return router


def _make_notification_handler(
    plan: Plan, article: Article, gateway: TelegramGateway, notify_chat_id: int
):
    async def handle(result: JobResult) -> None:
        if result.status == "failed":
            if result.job_type in ("generate_article", "regenerate_article"):
                article_id = await article.mark_generation_failed(result.job_id)
                if article_id is None:
                    logger.info("Ignoring stale Article failure for job_id=%s", result.job_id)
                    return
            await gateway.send_error_with_retry(
                notify_chat_id,
                f"Задача {result.job_type} завершилась ошибкой: {result.error}",
                result.job_id,
            )
            return

        output = result.output or {}
        if result.job_type == "generate_plan":
            topics = [
                TopicDraft(
                    title=t["title"], summary=t.get("summary", ""), keywords=t.get("keywords", [])
                )
                for t in output["topics"]
            ]
            plan_id = await plan.add_topics(output["week_label"], topics)
            view = await plan.get(plan_id)
            await gateway.send_plan(notify_chat_id, view)
        elif result.job_type == "regenerate_topic":
            plan_item_id = PlanItemId(output["plan_item_id"])
            await plan.apply_regeneration(
                plan_item_id,
                TopicDraft(
                    title=output["title"], summary=output["summary"], keywords=output["keywords"]
                ),
            )
            await gateway.send_notice(notify_chat_id, f"Тема обновлена: {output['title']}")
        elif result.job_type in ("generate_article", "regenerate_article"):
            article_id = ArticleId(output["article_id"])
            application = await article.record_version(
                article_id,
                GeneratedVersion(
                    content=output["content"],
                    prompt=output["prompt"],
                    model=output["model"],
                    tokens=output["tokens"],
                    cost=output["cost"],
                    source_job_id=result.job_id,
                ),
            )
            if application == "stale":
                logger.info("Ignoring stale Article result for job_id=%s", result.job_id)
                return
            view = await article.get(article_id)
            await gateway.send_article_ready(notify_chat_id, view)
        elif result.job_type == "generate_cover":
            plan_item_id = PlanItemId(output["plan_item_id"])
            image = base64.b64decode(output["image"])
            await plan.apply_cover(plan_item_id, image, output["mime_type"])
            item = await plan.get_item(plan_item_id)
            await gateway.send_cover(notify_chat_id, image, output["mime_type"], item.title)
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
        join_requests = JoinRequests(pool)
        await join_requests.ensure_schema()
        schedule_settings = ScheduleSettings(pool)
        await schedule_settings.ensure_schema()
        owner_settings = OwnerSettingsStore(pool)
        await owner_settings.ensure_schema()
        owner_settings_service = SettingsService(owner_settings)
        plan = Plan(pool, queue)
        await plan.ensure_schema()
        article = Article(pool, queue)
        await article.ensure_schema()

        bot = Bot(token=settings.telegram_bot_token)
        bot_client = _AiogramBotClient(bot)
        gateway = TelegramGateway(bot_client)
        comment_prompt = TelegramCommentPrompt(bot_client)
        plan_review = PlanReview(plan, comment_prompt)
        article_comment_prompt = TelegramCommentPrompt(bot_client, action="regenerate_article")
        article_regeneration = CommentGatedRegeneration[ArticleId](
            article.request_regeneration, article_comment_prompt
        )
        join_request_flow = JoinRequestFlow(join_requests, membership, gateway)

        # The scheduler must exist before _build_router so /set_schedule can reschedule its job.
        scheduler = AsyncIOScheduler()
        persisted_schedule = await schedule_settings.get()
        if persisted_schedule is not None:
            schedule_weekly_plan_trigger(
                scheduler,
                plan,
                tz=settings.timezone,
                day_of_week=persisted_schedule.day_of_week,
                hour=persisted_schedule.hour,
                minute=persisted_schedule.minute,
            )
        else:
            schedule_weekly_plan_trigger(scheduler, plan, tz=settings.timezone)
        scheduler.start()

        dispatcher = Dispatcher()
        dispatcher.include_router(
            _build_router(
                membership,
                plan,
                article,
                gateway,
                bot_client,
                plan_review,
                article_regeneration,
                join_request_flow,
                schedule_settings,
                owner_settings_service,
                queue,
                scheduler,
                settings,
            )
        )

        stop = asyncio.Event()
        register_shutdown(stop)

        notify_handler = _make_notification_handler(
            plan, article, gateway, settings.telegram_notify_chat_id
        )
        polling_task = asyncio.create_task(dispatcher.start_polling(bot, handle_signals=False))
        notifications_task = asyncio.create_task(
            run_notifications(queue, notify_handler, stop=stop)
        )

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
