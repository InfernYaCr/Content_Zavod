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
from dataclasses import dataclass
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
    PlanId,
    PlanItemId,
    TopicDraft,
)
from ..job_queue import JobQueue, JobResult, run_notifications
from ..migrations import run_migrations
from ..owner_settings import OwnerSettingsStore
from ..scheduling import (
    DEFAULT_DAY_OF_WEEK,
    DEFAULT_HOUR,
    DEFAULT_MINUTE,
    ScheduleConfig,
    ScheduleSettings,
    reconcile_weekly_plan,
    schedule_weekly_plan_trigger,
)
from ..settings import SettingsService
from ..telegram import (
    ACCESS_DENIED_TEXT,
    OWNER_ONLY_TEXT,
    ArticleId,
    BotClient,
    CallbackDispatcher,
    CommentGatedRegeneration,
    JoinRequestFlow,
    PlanReview,
    TelegramCommentPrompt,
    TelegramGateway,
    build_request_access_keyboard,
    deliver_plan_message,
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
from ..telegram.texts import job_failure_text
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
                    text = ACCESS_DENIED_TEXT if actual is None else OWNER_ONLY_TEXT
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
            await gateway.send_error(message.chat.id, ACCESS_DENIED_TEXT)
            return
        chat_id, user_id, text = message.chat.id, message.from_user.id, message.text or ""
        consumed = await plan_review.handle_comment_reply(chat_id, user_id, text)
        if not consumed:
            await article_regeneration.handle_comment_reply(chat_id, user_id, text)

    return router


@dataclass(frozen=True)
class _PlanDelivery:
    plan_id: PlanId


@dataclass(frozen=True)
class _NoticeDelivery:
    text: str


@dataclass(frozen=True)
class _ArticleDelivery:
    article_id: ArticleId


@dataclass(frozen=True)
class _CoverDelivery:
    plan_item_id: PlanItemId
    image: bytes
    mime_type: str


@dataclass(frozen=True)
class _ErrorDelivery:
    text: str
    job_id: int
    # Set when this failed Job was also part of an open batch (#91): a failure still has to
    # advance the shared progress message (and, if it was the batch's last Job, still trigger
    # the final burst for whatever else succeeded) - or a batch with any failed Job would never
    # close and its already-succeeded Статьи would never reach the delivery.
    batch_progress: _BatchProgressDelivery | _BatchDoneDelivery | None = None


@dataclass(frozen=True)
class _BatchProgressDelivery:
    """One Job of an open approve_all batch (#91) finished, but not the last one - the shared
    progress message needs editing, not a per-Job message."""

    plan_id: PlanId
    done: int
    total: int


@dataclass(frozen=True)
class _BatchDoneDelivery:
    """The last Job of an open approve_all batch (#91) finished - the progress message becomes
    the final "done" text, followed by every ready Статья and обложка together."""

    plan_id: PlanId
    total: int


_Delivery = (
    _PlanDelivery
    | _NoticeDelivery
    | _ArticleDelivery
    | _CoverDelivery
    | _ErrorDelivery
    | _BatchProgressDelivery
    | _BatchDoneDelivery
)


async def _advance_batch(
    plan: Plan, plan_id: PlanId
) -> _BatchProgressDelivery | _BatchDoneDelivery | None:
    """Advances one open batch's progress by one finished Job (#91). `None` means no batch is
    open for this Plan right now - the caller falls back to its own per-Job delivery."""
    progress = await plan.record_generation_progress(plan_id)
    if progress is None:
        return None
    done, total = progress
    if done >= total:
        return _BatchDoneDelivery(plan_id=plan_id, total=total)
    return _BatchProgressDelivery(plan_id=plan_id, done=done, total=total)


async def _apply_result(plan: Plan, article: Article, result: JobResult) -> _Delivery | None:
    """The DB half of notification handling (#73): applies a finished Job's result to
    domain state and returns what, if anything, still needs delivering to Telegram - `None`
    for a stale result nothing should be sent for. Pure DB writes only; no Telegram call
    happens here, so retrying this half alone (as a redelivered notification does) is exactly
    as idempotent as the domain operations it calls."""
    if result.status == "failed":
        batch_delivery: _BatchProgressDelivery | _BatchDoneDelivery | None = None
        failed_title: str | None = None
        failed_platform: str | None = None
        if result.job_type in ("generate_article", "regenerate_article"):
            article_id = await article.mark_generation_failed(result.job_id)
            if article_id is None:
                logger.info("Ignoring stale Article failure for job_id=%s", result.job_id)
                return None
            summary = await article.get_summary(article_id)
            failed_title, failed_platform = summary.title, summary.platform
            if result.job_type == "generate_article":
                batch_delivery = await _advance_batch(plan, await article.get_plan_id(article_id))
        elif result.job_type == "generate_cover":
            plan_item_id = await plan.mark_cover_generation_failed(result.job_id)
            if plan_item_id is None:
                logger.info("Ignoring stale cover failure for job_id=%s", result.job_id)
                return None
            failed_title = (await plan.get_item(plan_item_id)).title
            batch_delivery = await _advance_batch(
                plan, await plan.get_plan_id_for_item(plan_item_id)
            )
        # The chat gets a plain Russian «Не удалось …» (#89); the technical error stays in the log.
        logger.warning("Job %s (%s) failed: %s", result.job_id, result.job_type, result.error)
        return _ErrorDelivery(
            text=job_failure_text(result.job_type, title=failed_title, platform=failed_platform),
            job_id=result.job_id,
            batch_progress=batch_delivery,
        )

    output = result.output or {}
    if result.job_type == "generate_plan":
        topics = [
            TopicDraft(
                title=t["title"], summary=t.get("summary", ""), keywords=t.get("keywords", [])
            )
            for t in output["topics"]
        ]
        plan_id = await plan.add_topics(output["week_label"], topics)
        return _PlanDelivery(plan_id=plan_id)
    if result.job_type == "regenerate_topic":
        plan_item_id = PlanItemId(output["plan_item_id"])
        await plan.apply_regeneration(
            plan_item_id,
            TopicDraft(
                title=output["title"], summary=output["summary"], keywords=output["keywords"]
            ),
        )
        return _NoticeDelivery(text=f"Тема обновлена: {output['title']}")
    if result.job_type in ("generate_article", "regenerate_article"):
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
            return None
        if result.job_type == "generate_article" and application == "applied":
            batch_delivery = await _advance_batch(plan, await article.get_plan_id(article_id))
            if batch_delivery is not None:
                return batch_delivery
        return _ArticleDelivery(article_id=article_id)
    if result.job_type == "generate_cover":
        plan_item_id = PlanItemId(output["plan_item_id"])
        image = base64.b64decode(output["image"])
        await plan.apply_cover(plan_item_id, image, output["mime_type"])
        batch_delivery = await _advance_batch(plan, await plan.get_plan_id_for_item(plan_item_id))
        if batch_delivery is not None:
            return batch_delivery
        return _CoverDelivery(plan_item_id=plan_item_id, image=image, mime_type=output["mime_type"])

    logger.warning("No notification renderer for job_type=%r", result.job_type)
    return None


async def _deliver(
    plan: Plan,
    article: Article,
    gateway: TelegramGateway,
    notify_chat_id: int,
    delivery: _Delivery,
) -> None:
    """The Telegram half of notification handling (#73): turns an `_apply_result` outcome
    into the actual message(s). A Plan delivery goes through `deliver_plan_message`, which
    sends only the first time and edits the Plan's canonical message every time after -
    the fix for #73's duplicate-message gap."""
    if isinstance(delivery, _PlanDelivery):
        view = await plan.get(delivery.plan_id)
        await deliver_plan_message(plan, gateway, notify_chat_id, view)
    elif isinstance(delivery, _NoticeDelivery):
        await gateway.send_notice(notify_chat_id, delivery.text)
    elif isinstance(delivery, _ArticleDelivery):
        view = await article.get(delivery.article_id)
        await gateway.send_article_ready(notify_chat_id, view)
    elif isinstance(delivery, _CoverDelivery):
        item = await plan.get_item(delivery.plan_item_id)
        await gateway.send_cover(notify_chat_id, delivery.image, delivery.mime_type, item.title)
    elif isinstance(delivery, _ErrorDelivery):
        await gateway.send_error_with_retry(notify_chat_id, delivery.text, delivery.job_id)
        if delivery.batch_progress is not None:
            await _deliver_batch(plan, article, gateway, notify_chat_id, delivery.batch_progress)
    elif isinstance(delivery, _BatchProgressDelivery | _BatchDoneDelivery):
        await _deliver_batch(plan, article, gateway, notify_chat_id, delivery)


async def _deliver_batch(
    plan: Plan,
    article: Article,
    gateway: TelegramGateway,
    notify_chat_id: int,
    delivery: _BatchProgressDelivery | _BatchDoneDelivery,
) -> None:
    """Shared by the dedicated batch deliveries and a failed Job that was also part of an open
    batch (#91) - a failure still has to advance/finalize the shared progress message, and if
    it happened to be the batch's last Job, still trigger the burst for whatever else in the
    batch succeeded."""
    done = delivery.total if isinstance(delivery, _BatchDoneDelivery) else delivery.done
    ref = await plan.get_progress_message_ref(delivery.plan_id)
    if ref is not None:
        # `ref` is None only in the narrow crash window (#91, same class as #73's plan-message
        # gap) between start_generation_batch opening the batch and its progress message
        # actually being recorded - the done/total count itself is still correct either way.
        await gateway.edit_generation_progress(ref.chat_id, ref.message_id, done, delivery.total)
    if isinstance(delivery, _BatchDoneDelivery):
        for view in await article.list_for_plan(delivery.plan_id):
            await gateway.send_article_ready(notify_chat_id, view)
        for cover in await plan.list_covers_for_plan(delivery.plan_id):
            await gateway.send_cover(notify_chat_id, cover.image, cover.mime_type, cover.title)


def _make_notification_handler(
    plan: Plan, article: Article, gateway: TelegramGateway, notify_chat_id: int
):
    async def handle(result: JobResult) -> None:
        delivery = await _apply_result(plan, article, result)
        if delivery is not None:
            await _deliver(plan, article, gateway, notify_chat_id, delivery)

    return handle


async def main(settings: Settings | None = None) -> None:
    logging.basicConfig(level=logging.INFO)
    settings = settings or load_settings()

    pool = await asyncpg.create_pool(dsn=settings.postgres_dsn)
    assert pool is not None
    try:
        await run_migrations(pool)

        queue = JobQueue(pool)
        membership = Membership(pool)
        join_requests = JoinRequests(pool)
        schedule_settings = ScheduleSettings(pool)
        owner_settings = OwnerSettingsStore(pool)
        owner_settings_service = SettingsService(owner_settings)
        plan = Plan(pool, queue)
        article = Article(pool, queue)

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
        resolved_schedule = persisted_schedule or ScheduleConfig(
            day_of_week=DEFAULT_DAY_OF_WEEK, hour=DEFAULT_HOUR, minute=DEFAULT_MINUTE
        )
        schedule_weekly_plan_trigger(
            scheduler,
            plan,
            tz=settings.timezone,
            day_of_week=resolved_schedule.day_of_week,
            hour=resolved_schedule.hour,
            minute=resolved_schedule.minute,
        )
        # Recover a trigger missed while the process was down (#72) before the live
        # cron job takes over for future weeks.
        await reconcile_weekly_plan(
            plan,
            tz=settings.timezone,
            day_of_week=resolved_schedule.day_of_week,
            hour=resolved_schedule.hour,
            minute=resolved_schedule.minute,
        )
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
