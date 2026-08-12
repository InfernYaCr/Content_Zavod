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

from ..access import AccessError, JoinRequests, Membership, Role
from ..config import Settings, load_settings
from ..domain import (
    PLATFORMS,
    Article,
    ArticleId,
    DomainError,
    GeneratedVersion,
    Plan,
    PlanId,
    PlanItemId,
    TopicDraft,
)
from ..job_queue import JobId, JobQueue, JobResult, run_notifications
from ..owner_settings import OwnerSettingsStore
from ..scheduling import ScheduleSettings, schedule_weekly_plan_trigger
from ..settings import SettingsService
from ..telegram import (
    BotClient,
    CommentGatedRegeneration,
    JoinRequestFlow,
    PlanReview,
    TelegramCommentPrompt,
    TelegramGateway,
    build_request_access_keyboard,
    decode_callback_data,
    decode_export_id,
    decode_page_id,
    handle_cancel_regenerate_plan,
    handle_confirm_regenerate_plan,
    handle_directions_command,
    handle_generate_plan_command,
    handle_history_command,
    handle_history_page,
    handle_history_version,
    handle_history_versions,
    handle_history_week,
    handle_members_command,
    handle_niche_command,
    handle_persona_command,
    handle_persona_template_callback,
    handle_schedule_command,
    handle_set_directions_command,
    handle_set_niche_command,
    handle_set_persona_command,
    handle_set_schedule_command,
    handle_settings_command,
    handle_topic_command,
    render_help_text,
    sync_commands,
)
from ._process import register_shutdown

logger = logging.getLogger(__name__)

_ACCESS_DENIED_TEXT = "Доступ запрещён. Обратитесь к владельцу бота, чтобы получить роль."
_OWNER_ONLY_TEXT = "Эта команда доступна только владельцу."


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


async def _role_for(
    membership: Membership, gateway: TelegramGateway, chat_id: int, telegram_id: int
) -> Role | None:
    role = await membership.role_for(telegram_id)
    if role is None:
        await gateway.send_error(chat_id, _ACCESS_DENIED_TEXT)
    return role


async def _generate_articles_for_approved_plan(
    plan: Plan, article: Article, plan_id: PlanId
) -> None:
    """Fan out each approved Тема into one Статья per Площадка and enqueue its `generate_article`
    Job (#14), plus one `generate_cover` Job per Тема (#15 - the whole week's content, including
    covers, is generated together, so no separate manual trigger is needed for the common case).
    Reads current DB state (`Plan.approved_items`) rather than acting only on items this call just
    approved, and both `Article.request_generation` and `Plan.request_cover` are themselves
    idempotent - so replaying this for an already-approved Plan (a retried `approve_all` callback,
    or a crash between approving and enqueueing) creates neither duplicate Статьи/обложки nor
    duplicate Jobs."""
    for item in await plan.approved_items(plan_id):
        await plan.request_cover(item.id)
        for platform in PLATFORMS:
            await article.request_generation(
                plan_id, item.id, item.title, item.summary, item.keywords, platform
            )


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
    async def on_help(message: Message) -> None:
        if message.from_user is None:
            return
        role = await _role_for(membership, gateway, message.chat.id, message.from_user.id)
        if role is None:
            return
        await gateway.send_notice(message.chat.id, render_help_text(role))

    @router.message(Command("topic"))
    async def on_topic(message: Message) -> None:
        if message.from_user is None:
            return
        if await _role_for(membership, gateway, message.chat.id, message.from_user.id) is None:
            return
        parts = (message.text or "").split(maxsplit=1)
        text = parts[1] if len(parts) > 1 else ""
        await handle_topic_command(plan, gateway, message.chat.id, text, tz=settings.timezone)

    @router.message(Command("generate_plan"))
    async def on_generate_plan(message: Message) -> None:
        if message.from_user is None:
            return
        if await _role_for(membership, gateway, message.chat.id, message.from_user.id) is None:
            return
        await handle_generate_plan_command(plan, gateway, message.chat.id, tz=settings.timezone)

    @router.message(Command("history"))
    async def on_history(message: Message) -> None:
        if message.from_user is None:
            return
        if await _role_for(membership, gateway, message.chat.id, message.from_user.id) is None:
            return
        await handle_history_command(plan, gateway, message.chat.id)

    @router.message(Command("members"))
    async def on_members(message: Message) -> None:
        if message.from_user is None:
            return
        role = await _role_for(membership, gateway, message.chat.id, message.from_user.id)
        if role is None:
            return
        if role != "owner":
            await gateway.send_error(message.chat.id, _OWNER_ONLY_TEXT)
            return
        await handle_members_command(membership, gateway, message.chat.id)

    @router.message(Command("schedule"))
    async def on_schedule(message: Message) -> None:
        if message.from_user is None:
            return
        role = await _role_for(membership, gateway, message.chat.id, message.from_user.id)
        if role is None:
            return
        if role != "owner":
            await gateway.send_error(message.chat.id, _OWNER_ONLY_TEXT)
            return
        await handle_schedule_command(schedule_settings, gateway, message.chat.id)

    @router.message(Command("set_schedule"))
    async def on_set_schedule(message: Message, command: CommandObject) -> None:
        if message.from_user is None:
            return
        role = await _role_for(membership, gateway, message.chat.id, message.from_user.id)
        if role is None:
            return
        if role != "owner":
            await gateway.send_error(message.chat.id, _OWNER_ONLY_TEXT)
            return
        await handle_set_schedule_command(
            schedule_settings,
            scheduler,
            gateway,
            message.chat.id,
            command.args or "",
            tz=settings.timezone,
        )

    @router.message(Command("niche"))
    async def on_niche(message: Message) -> None:
        if message.from_user is None:
            return
        role = await _role_for(membership, gateway, message.chat.id, message.from_user.id)
        if role is None:
            return
        if role != "owner":
            await gateway.send_error(message.chat.id, _OWNER_ONLY_TEXT)
            return
        await handle_niche_command(owner_settings_service, gateway, message.chat.id)

    @router.message(Command("set_niche"))
    async def on_set_niche(message: Message, command: CommandObject) -> None:
        if message.from_user is None:
            return
        role = await _role_for(membership, gateway, message.chat.id, message.from_user.id)
        if role is None:
            return
        if role != "owner":
            await gateway.send_error(message.chat.id, _OWNER_ONLY_TEXT)
            return
        await handle_set_niche_command(
            owner_settings_service, gateway, message.chat.id, command.args or ""
        )

    @router.message(Command("directions"))
    async def on_directions(message: Message) -> None:
        if message.from_user is None:
            return
        role = await _role_for(membership, gateway, message.chat.id, message.from_user.id)
        if role is None:
            return
        if role != "owner":
            await gateway.send_error(message.chat.id, _OWNER_ONLY_TEXT)
            return
        await handle_directions_command(owner_settings_service, gateway, message.chat.id)

    @router.message(Command("set_directions"))
    async def on_set_directions(message: Message, command: CommandObject) -> None:
        if message.from_user is None:
            return
        role = await _role_for(membership, gateway, message.chat.id, message.from_user.id)
        if role is None:
            return
        if role != "owner":
            await gateway.send_error(message.chat.id, _OWNER_ONLY_TEXT)
            return
        await handle_set_directions_command(
            owner_settings_service, gateway, message.chat.id, command.args or ""
        )

    @router.message(Command("persona"))
    async def on_persona(message: Message) -> None:
        if message.from_user is None:
            return
        role = await _role_for(membership, gateway, message.chat.id, message.from_user.id)
        if role is None:
            return
        if role != "owner":
            await gateway.send_error(message.chat.id, _OWNER_ONLY_TEXT)
            return
        await handle_persona_command(owner_settings_service, gateway, message.chat.id)

    @router.message(Command("set_persona"))
    async def on_set_persona(message: Message, command: CommandObject) -> None:
        if message.from_user is None:
            return
        role = await _role_for(membership, gateway, message.chat.id, message.from_user.id)
        if role is None:
            return
        if role != "owner":
            await gateway.send_error(message.chat.id, _OWNER_ONLY_TEXT)
            return
        await handle_set_persona_command(
            owner_settings_service, gateway, message.chat.id, command.args or ""
        )

    @router.message(Command("settings"))
    async def on_settings(message: Message) -> None:
        if message.from_user is None:
            return
        role = await _role_for(membership, gateway, message.chat.id, message.from_user.id)
        if role is None:
            return
        if role != "owner":
            await gateway.send_error(message.chat.id, _OWNER_ONLY_TEXT)
            return
        await handle_settings_command(owner_settings_service, gateway, message.chat.id)

    @router.callback_query()
    async def on_callback(callback: CallbackQuery) -> None:
        if callback.from_user is None or callback.message is None:
            return
        chat_id = callback.message.chat.id
        message_id = callback.message.message_id
        user_id = callback.from_user.id

        try:
            action, id_ = decode_callback_data(callback.data or "")
        except ValueError:
            await callback.answer()
            return

        if action == "request_access":
            await callback.answer()
            await join_request_flow.request_access(user_id, callback.from_user.username)
            await gateway.edit_notice(
                chat_id, message_id, "Заявка отправлена. Ожидайте одобрения владельца."
            )
            return

        role = await membership.role_for(user_id)
        if role is None:
            await callback.answer(_ACCESS_DENIED_TEXT, show_alert=True)
            return

        try:
            if action in ("approve_join", "decline_join"):
                if role != "owner":
                    await callback.answer(_OWNER_ONLY_TEXT, show_alert=True)
                    return
                await callback.answer()
                resolver_name = callback.from_user.full_name
                if action == "approve_join":
                    resolved = await join_request_flow.handle_approve(
                        user_id, resolver_name, int(id_)
                    )
                else:
                    resolved = await join_request_flow.handle_decline(
                        user_id, resolver_name, int(id_)
                    )
                if resolved is not None and resolved.status == "approved":
                    await sync_commands(bot_client, resolved.telegram_id, "content_manager")
            elif action == "remove_member":
                if role != "owner":
                    await callback.answer(_OWNER_ONLY_TEXT, show_alert=True)
                    return
                await callback.answer()
                await membership.remove_member(int(id_))
            elif action == "persona_template":
                if role != "owner":
                    await callback.answer(_OWNER_ONLY_TEXT, show_alert=True)
                    return
                await callback.answer()
                await handle_persona_template_callback(
                    owner_settings_service, gateway, chat_id, int(id_)
                )
            elif action == "page":
                await callback.answer()
                page_plan_id, page = decode_page_id(id_)
                view = await plan.get(PlanId(page_plan_id))
                await gateway.edit_plan(chat_id, message_id, view, page=page)
            elif action == "history_page":
                await callback.answer()
                await handle_history_page(plan, gateway, chat_id, message_id, int(id_))
            elif action == "history_week":
                await callback.answer()
                await handle_history_week(plan, article, gateway, chat_id, message_id, id_)
            elif action == "history_versions":
                await callback.answer()
                await handle_history_versions(article, gateway, chat_id, message_id, id_)
            elif action == "history_version":
                await callback.answer()
                await handle_history_version(article, gateway, chat_id, message_id, id_)
            elif action == "confirm_regenerate_plan":
                await callback.answer()
                await handle_confirm_regenerate_plan(
                    plan, gateway, chat_id, message_id, PlanId(id_)
                )
            elif action == "cancel_regenerate_plan":
                await callback.answer()
                await handle_cancel_regenerate_plan(gateway, chat_id, message_id)
            elif action == "retry":
                await callback.answer()
                await queue.retry(JobId(int(id_)))
            elif action == "regenerate_article":
                will_enqueue = article_regeneration.has_matching_pending(
                    chat_id, user_id, ArticleId(id_)
                )
                await callback.answer("Принято, генерирую..." if will_enqueue else None)
                if will_enqueue:
                    await gateway.edit_notice(chat_id, message_id, "⏳ Генерирую...")
                await article_regeneration.request(chat_id, user_id, ArticleId(id_))
            elif action == "approve":
                await callback.answer()
                # Accepting a ready Статья: no comment-wait, just the transition to "exported".
                await article.mark_exported(ArticleId(id_))
            elif action == "request_cover":
                await callback.answer("Генерирую обложку...")
                await plan.request_cover(PlanItemId(id_))
            elif action == "export_article":
                await callback.answer()
                export_article_id, export_format = decode_export_id(id_)
                view = await article.get(ArticleId(export_article_id))
                await gateway.send_article_document(chat_id, view, export_format)
            elif action == "regenerate":
                will_enqueue = plan_review.will_enqueue_regeneration(
                    chat_id, user_id, PlanItemId(id_)
                )
                await callback.answer("Принято, генерирую..." if will_enqueue else None)
                if will_enqueue:
                    await gateway.edit_notice(chat_id, message_id, "⏳ Генерирую...")
                await plan_review.handle_action(chat_id, user_id, PlanItemId(id_), action)
            elif action == "approve_all":
                await callback.answer()
                await plan_review.handle_action(chat_id, user_id, PlanItemId(id_), action)
                await _generate_articles_for_approved_plan(plan, article, PlanId(id_))
            elif action == "delete":
                await callback.answer()
                await plan_review.handle_action(chat_id, user_id, PlanItemId(id_), action)
                await gateway.send_notice(chat_id, "Тема удалена.")
            else:
                await callback.answer()
                await plan_review.handle_action(chat_id, user_id, PlanItemId(id_), action)
        except (DomainError, AccessError) as exc:
            await callback.answer(str(exc), show_alert=True)

    @router.message()
    async def on_message(message: Message) -> None:
        if message.from_user is None:
            return
        if await _role_for(membership, gateway, message.chat.id, message.from_user.id) is None:
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
