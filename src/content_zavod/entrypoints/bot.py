"""bot_main: the process that talks to Telegram (ADR-0004).

Every update is served in the Пространство of whoever sent it (ADR-0015): `/start` in a private
chat creates the Workspace (`Workspaces.get_or_create`), every other update resolves it by
`from_user.id`, and that `Workspace` is passed down to every flow - never taken from
`callback_data`, text or a Job payload. The bot works in private chats only: in a group it
answers `/start` with a pointer to the private chat and ignores the rest. Runs
`run_notifications` (#2) to deliver each finished Job's result to the private chat of the
Workspace that owns the Job (a 403 marks it `bot_blocked_at`), and the Расписание tick
(`schedule_weekly_plan_tick`, #7, ADR-0015) via APScheduler in the same process. Never runs a
Job Handler itself (see entrypoints/worker.py) - it only enqueues (through Plan/Article) and
renders results.
"""

from __future__ import annotations

import asyncio
import base64
import logging
from collections.abc import Awaitable, Callable, Collection
from dataclasses import dataclass
from functools import wraps
from typing import Protocol

import asyncpg
from aiogram import Bot, Dispatcher, Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import Command, CommandObject
from aiogram.types import (
    BotCommand,
    BotCommandScopeChat,
    BotCommandScopeDefault,
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    Message,
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler

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
from ..owner_settings import BotStateStore, WorkspaceSettingsStore
from ..scheduling import ScheduleSettings, schedule_weekly_plan_tick
from ..settings import OnboardingState, SettingsService
from ..telegram import (
    MENU_COMMANDS,
    ArticleId,
    BotClient,
    CallbackDispatcher,
    CommentGatedRegeneration,
    InputPrompt,
    MainMenu,
    MessageGone,
    PlanReview,
    SettingsScreen,
    TelegramCommentPrompt,
    TelegramGateway,
    build_open_menu_keyboard,
    deliver_plan_hub,
    deliver_plan_message,
    handle_generate_plan_command,
    handle_history_command,
    handle_topic_command,
    sync_admin_commands,
    unpack_callback_query,
)
from ..telegram.article_card import ArticlePagePublisher, send_article_card
from ..telegram.asset_photos import AssetPhotos
from ..telegram.gateway import SentPhoto, format_week_range
from ..telegram.guide import Guide
from ..telegram.onboarding import Onboarding
from ..telegram.pending_inputs import PendingInputs
from ..telegram.texts import (
    ADMIN_PENDING_TEXT,
    BOT_DESCRIPTION,
    BOT_SHORT_DESCRIPTION,
    GROUP_START_TEXT,
    HELP_TEXT,
    NO_WORKSPACE_TEXT,
    OPEN_PRIVATE_CHAT_BUTTON,
    STALE_BUTTON,
    UNKNOWN_MESSAGE_TEXT,
    job_failure_text,
)
from ..telegraph import HttpxTelegraphClient, TelegraphPublisher, project_footer
from ..workspaces import Workspace, WorkspaceId, Workspaces
from ._process import register_shutdown

logger = logging.getLogger(__name__)


# Telegram's own error descriptions (aiogram keeps them in `TelegramBadRequest.message`, e.g.
# "Bad Request: message to edit not found"): the edited message was deleted, or the bot may no
# longer edit it (#106).
_MESSAGE_GONE = ("message to edit not found", "message can't be edited")


def _is_not_modified(exc: TelegramBadRequest) -> bool:
    """An unchanged redraw - success (ADR-0014)."""
    return "message is not modified" in exc.message


def _is_message_gone(exc: TelegramBadRequest) -> bool:
    return any(description in exc.message for description in _MESSAGE_GONE)


def _largest_photo_id(message: Message) -> str | None:
    """The biggest size's `file_id` - the one worth caching for a re-send (#114)."""
    return message.photo[-1].file_id if message.photo else None


class _AiogramBotClient:
    """Adapts `aiogram.Bot` to the narrow `BotClient` protocol the telegram layer depends on."""

    def __init__(self, bot: Bot) -> None:
        self._bot = bot

    async def send_message(
        self,
        chat_id: int,
        text: str,
        reply_markup: InlineKeyboardMarkup | None = None,
    ) -> int:
        message = await self._bot.send_message(chat_id, text, reply_markup=reply_markup)
        return message.message_id

    async def send_document(
        self, chat_id: int, document: BufferedInputFile, caption: str | None = None
    ) -> None:
        await self._bot.send_document(chat_id, document, caption=caption)

    async def send_photo(
        self,
        chat_id: int,
        photo: BufferedInputFile | str,
        caption: str | None = None,
        reply_markup: InlineKeyboardMarkup | None = None,
    ) -> SentPhoto:
        message = await self._bot.send_photo(
            chat_id, photo, caption=caption, reply_markup=reply_markup
        )
        return SentPhoto(message.message_id, _largest_photo_id(message))

    async def edit_message_media(
        self,
        chat_id: int,
        message_id: int,
        photo: BufferedInputFile | str,
        caption: str | None = None,
        reply_markup: InlineKeyboardMarkup | None = None,
    ) -> str | None:
        try:
            result = await self._bot.edit_message_media(
                InputMediaPhoto(media=photo, caption=caption),
                chat_id=chat_id,
                message_id=message_id,
                reply_markup=reply_markup,
            )
        except TelegramBadRequest as exc:
            if _is_message_gone(exc):
                raise MessageGone(chat_id, message_id) from exc
            if not _is_not_modified(exc):
                raise
            return None
        return _largest_photo_id(result) if isinstance(result, Message) else None

    async def pin_chat_message(self, chat_id: int, message_id: int) -> None:
        await self._bot.pin_chat_message(chat_id, message_id, disable_notification=True)

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
            if _is_message_gone(exc):
                raise MessageGone(chat_id, message_id) from exc
            if not _is_not_modified(exc):
                raise

    async def edit_message_reply_markup(
        self, chat_id: int, message_id: int, reply_markup: InlineKeyboardMarkup | None = None
    ) -> None:
        try:
            await self._bot.edit_message_reply_markup(
                chat_id=chat_id, message_id=message_id, reply_markup=reply_markup
            )
        except TelegramBadRequest as exc:
            if _is_message_gone(exc):
                raise MessageGone(chat_id, message_id) from exc
            if not _is_not_modified(exc):
                raise

    async def delete_message(self, chat_id: int, message_id: int) -> None:
        try:
            await self._bot.delete_message(chat_id, message_id)
        except TelegramBadRequest as exc:
            # Already deleted, or older than Telegram's 48h delete window: nothing to tidy.
            logger.info("could not delete message %s in chat %s: %s", message_id, chat_id, exc)

    async def set_my_commands(
        self, commands: list[BotCommand], *, scope: BotCommandScopeChat
    ) -> None:
        await self._bot.set_my_commands(commands, scope=scope)


class WorkspaceDirectory(Protocol):
    """What the bot needs from `Workspaces`: create one on /start, find the sender's otherwise,
    find a Job's for its notification, and mark one whose owner blocked the bot."""

    async def get_or_create(
        self, owner_telegram_id: int, *, timezone: str, owner_username: str | None = None
    ) -> Workspace: ...

    async def get(self, workspace_id: WorkspaceId) -> Workspace | None: ...

    async def get_by_owner(self, owner_telegram_id: int) -> Workspace | None: ...

    async def mark_blocked(self, workspace_id: WorkspaceId) -> None: ...


async def answer_text(
    workspace: Workspace,
    chat_id: int,
    user_id: int,
    text: str,
    *,
    plan_review: PlanReview,
    article_regeneration: CommentGatedRegeneration[ArticleId],
    settings_screen: SettingsScreen,
    main_menu: MainMenu,
    onboarding: Onboarding,
    prompts: InputPrompt,
    gateway: TelegramGateway,
) -> None:
    """A text in the private chat that no command took: a comment for a pending regeneration
    (#4/#9), or an answer to a menu/Настройки/onboarding question (#95) - each looked up in the
    sender's own Пространство, so it can only ever close that Workspace's wait (ADR-0015). A
    `/command` the bot doesn't know (or one not meant for this user, like /admin) is never an
    answer: it gets the same pointer to the menu as an unsolicited message."""
    command = text.startswith("/")
    consumed = not command and (
        await plan_review.handle_comment_reply(workspace.id, chat_id, user_id, text)
        or await article_regeneration.handle_comment_reply(workspace.id, chat_id, user_id, text)
        or await settings_screen.handle_reply(workspace, chat_id, user_id, text)
        or await main_menu.handle_reply(workspace, chat_id, user_id, text)
        or await onboarding.handle_reply(workspace, chat_id, user_id, text)
    )
    if consumed:
        return
    if command or not await prompts.is_waiting(workspace.id, chat_id, user_id):
        # Nothing was asked: point at the menu rather than staying silent (#95). A live wait
        # no handler above took (a flow added later and not wired in here) is left alone.
        await gateway.send_notice(
            chat_id, UNKNOWN_MESSAGE_TEXT, reply_markup=build_open_menu_keyboard()
        )


def _private_chat_button(bot_username: str | None) -> InlineKeyboardMarkup | None:
    if not bot_username:
        return None
    button = InlineKeyboardButton(text=OPEN_PRIVATE_CHAT_BUTTON, url=f"https://t.me/{bot_username}")
    return InlineKeyboardMarkup(inline_keyboard=[[button]])


def _build_router(
    workspaces: WorkspaceDirectory,
    plan: Plan,
    article: Article,
    gateway: TelegramGateway,
    bot_client: BotClient,
    plan_review: PlanReview,
    article_regeneration: CommentGatedRegeneration[ArticleId],
    settings_screen: SettingsScreen,
    main_menu: MainMenu,
    prompts: InputPrompt,
    queue: JobQueue,
    settings: Settings,
    onboarding: Onboarding,
    guide: Guide,
    *,
    publisher: ArticlePagePublisher | None = None,
    bot_username: str | None = None,
) -> Router:
    router = Router()
    admin_ids: Collection[int] = settings.global_admin_telegram_ids

    async def private_workspace(message: Message) -> Workspace | None:
        """The sender's Пространство for a private-chat update; `None` (and nothing done) in a
        group, or - with a nudge to /start - for someone who hasn't pressed it yet."""
        if message.from_user is None or message.chat.type != "private":
            return None
        workspace = await workspaces.get_by_owner(message.from_user.id)
        if workspace is None:
            await gateway.send_notice(message.chat.id, NO_WORKSPACE_TEXT)
        return workspace

    def in_workspace(
        handler: Callable[..., Awaitable[None]],
    ) -> Callable[..., Awaitable[None]]:
        """Resolve the sender's Workspace and pass it as `workspace`. `@wraps` keeps the
        handler's own signature visible to aiogram's argument injection, so `command` still
        reaches it unchanged."""

        @wraps(handler)
        async def wrapper(message: Message, **kwargs: object) -> None:
            workspace = await private_workspace(message)
            if workspace is None:
                return
            assert message.from_user is not None
            # Any command interrupts the onboarding wizard (#96): its open question is
            # dropped, so the next message isn't taken for an answer - never blocked.
            await onboarding.interrupt(workspace, message.chat.id, message.from_user.id)
            await handler(message, workspace=workspace, **kwargs)

        return wrapper

    @router.message(Command("start"))
    async def on_start(message: Message) -> None:
        if message.from_user is None:
            return
        chat_id = message.chat.id
        if message.chat.type != "private":
            await gateway.send_message(
                chat_id, GROUP_START_TEXT, reply_markup=_private_chat_button(bot_username)
            )
            return
        workspace = await workspaces.get_or_create(
            message.from_user.id,
            timezone=settings.timezone.key,
            owner_username=message.from_user.username,
        )
        await onboarding.interrupt(workspace, chat_id, message.from_user.id)
        # A Пространство that isn't set up yet gets the wizard's intro instead (#96).
        if await onboarding.offer(workspace, chat_id):
            return
        await main_menu.send(chat_id, welcome=True)

    async def is_global_admin(message: Message) -> bool:
        return (
            message.from_user is not None
            and message.chat.type == "private"
            and message.from_user.id in admin_ids
        )

    @router.message(Command("admin"), is_global_admin)
    async def on_admin(message: Message) -> None:
        # Anyone else's /admin falls through to `on_message`: an unknown command (ADR-0015).
        await gateway.send_notice(message.chat.id, ADMIN_PENDING_TEXT)

    @router.message(Command("menu"))
    @in_workspace
    async def on_menu(message: Message, workspace: Workspace) -> None:
        await main_menu.send(message.chat.id)

    @router.message(Command("help"))
    @in_workspace
    async def on_help(message: Message, workspace: Workspace) -> None:
        try:
            await guide.send(message.chat.id)
        except Exception:
            # Not even its text went out: the short help, so /help never stays silent.
            logger.warning("could not send the guide", exc_info=True)
            await gateway.send_notice(
                message.chat.id, HELP_TEXT, reply_markup=build_open_menu_keyboard()
            )

    @router.message(Command("topic"))
    @in_workspace
    async def on_topic(message: Message, workspace: Workspace) -> None:
        assert message.from_user is not None
        parts = (message.text or "").split(maxsplit=1)
        text = parts[1] if len(parts) > 1 else ""
        if not text.strip():
            # A bare /topic asks for the Тема, like «✍️ Предложить Тему» (#95).
            await main_menu.ask_topic(workspace, message.chat.id, message.from_user.id)
            return
        await handle_topic_command(plan, gateway, workspace, message.chat.id, text)

    @router.message(Command("generate_plan"))
    @in_workspace
    async def on_generate_plan(message: Message, workspace: Workspace) -> None:
        await handle_generate_plan_command(plan, gateway, workspace, message.chat.id, queue=queue)

    @router.message(Command("history"))
    @in_workspace
    async def on_history(message: Message, workspace: Workspace) -> None:
        await handle_history_command(plan, gateway, workspace.id, message.chat.id)

    # Hidden aliases (#95): no longer in Telegram's command menu - the Экран Настроек and the
    # Расписание screen replace them - but kept working for muscle memory and older messages.
    @router.message(Command("schedule"))
    @in_workspace
    async def on_schedule(message: Message, workspace: Workspace) -> None:
        await settings_screen.send_schedule(workspace, message.chat.id)

    @router.message(Command("set_schedule"))
    @in_workspace
    async def on_set_schedule(
        message: Message, command: CommandObject, workspace: Workspace
    ) -> None:
        await settings_screen.set_schedule_command(workspace, message.chat.id, command.args or "")

    @router.message(Command("settings", "niche", "persona", "project", "audience"))
    @in_workspace
    async def on_settings(message: Message, workspace: Workspace) -> None:
        await settings_screen.send(workspace, message.chat.id)

    @router.message(Command("set_niche", "set_persona", "set_project", "set_audience"))
    @in_workspace
    async def on_set_setting(
        message: Message, command: CommandObject, workspace: Workspace
    ) -> None:
        assert message.from_user is not None
        key = command.command.removeprefix("set_")
        await settings_screen.apply_command(
            workspace, message.chat.id, message.from_user.id, key, command.args or ""
        )

    dispatcher = CallbackDispatcher(
        plan,
        article,
        gateway,
        bot_client,
        plan_review,
        article_regeneration,
        settings_screen,
        queue,
        main_menu,
        prompts,
        onboarding,
        guide=guide,
        publisher=publisher,
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
        # A private chat's id is its user's id. A button anywhere else - the old team chat - or
        # from someone without a Пространство acts on nothing (ADR-0015).
        workspace = (
            await workspaces.get_by_owner(callback_input.user_id)
            if callback_input.chat_id == callback_input.user_id
            else None
        )
        if workspace is None:
            await callback.answer(STALE_BUTTON, show_alert=True)
            return
        await dispatcher.dispatch(workspace, callback_input, callback.answer)

    @router.message()
    async def on_message(message: Message) -> None:
        """Any other message in a private chat: see `answer_text`. A sticker/photo/voice is
        not a comment - a wait stays open."""
        workspace = await private_workspace(message)
        if workspace is None or message.text is None:
            return
        assert message.from_user is not None
        await answer_text(
            workspace,
            message.chat.id,
            message.from_user.id,
            message.text,
            plan_review=plan_review,
            article_regeneration=article_regeneration,
            settings_screen=settings_screen,
            main_menu=main_menu,
            onboarding=onboarding,
            prompts=prompts,
            gateway=gateway,
        )

    return router


@dataclass(frozen=True)
class _PlanDelivery:
    plan_id: PlanId


@dataclass(frozen=True)
class _HubDelivery:
    """Redraw an approved Plan's message as its Хаб (#91) - after every Статья/cover result,
    success or failure. Progress is read back from the DB, never counted, so delivering this
    twice for one Job (a redelivered notification) is harmless."""

    plan_id: PlanId


@dataclass(frozen=True)
class _NoticeDelivery:
    text: str


@dataclass(frozen=True)
class _ArticleDelivery:
    """A Статья card, sent only for a by-hand «✏️ Доработать» (`regenerate_article`): the
    person who asked is looking at the bottom of the chat. The approval fan-out's Статьи are
    reached through the Хаб instead (#91)."""

    article_id: ArticleId


@dataclass(frozen=True)
class _PagePublishDelivery:
    """Publish a fan-out Статья's Страница для чтения (#92) as soon as it's ready, without a
    message: the Хаб's result card then links it straight away as «📖» (#91)."""

    article_id: ArticleId


@dataclass(frozen=True)
class _CoverDelivery:
    """A cover photo, sent only for a by-hand «🖼 Обложка» re-request - same reasoning as
    `_ArticleDelivery`."""

    plan_item_id: PlanItemId
    image: bytes
    mime_type: str


@dataclass(frozen=True)
class _ErrorDelivery:
    text: str
    job_id: int


_Delivery = (
    _PlanDelivery
    | _HubDelivery
    | _NoticeDelivery
    | _ArticleDelivery
    | _PagePublishDelivery
    | _CoverDelivery
    | _ErrorDelivery
)


def _empty_plan_text(week_label: str, empty_reason: str | None) -> str:
    """Why a `generate_plan` run came back with no Темы, and what to do about it (#84)."""
    if empty_reason == "all_recently_used":
        why = "все подобранные Темы недавно уже были в Планах"
    else:
        why = "не нашлось подходящих Тем"
    return (
        f"План на {format_week_range(week_label)} не создан: {why}.\n"
        "Предложите Тему сами: /topic <текст>"
    )


async def _apply_failure(plan: Plan, article: Article, result: JobResult) -> list[_Delivery]:
    """A failed Job: the fan-out's own Статьи/covers only turn ❌ in the Хаб (it carries
    «🔁 Повторить»); a failure of something someone asked for by hand - «✏️ Доработать», a
    «🖼 Обложка» re-request, a Plan/Тема generation - also gets its «Не удалось …» message."""
    # The chat gets a plain Russian «Не удалось …» (#89); the technical error stays in the log.
    logger.warning("Job %s (%s) failed: %s", result.job_id, result.job_type, result.error)
    ws = result.workspace_id
    if result.job_type in ("generate_article", "regenerate_article"):
        article_id = await article.mark_generation_failed(ws, result.job_id)
        if article_id is None:
            logger.info("Ignoring stale Article failure for job_id=%s", result.job_id)
            return []
        hub = _HubDelivery(plan_id=await article.get_plan_id(ws, article_id))
        if result.job_type == "generate_article":
            return [hub]
        summary = await article.get_summary(ws, article_id)
        text = job_failure_text(result.job_type, title=summary.title, platform=summary.platform)
        return [_ErrorDelivery(text=text, job_id=result.job_id), hub]
    if result.job_type == "generate_cover":
        plan_item_id = await plan.mark_cover_generation_failed(ws, result.job_id)
        if plan_item_id is None:
            logger.info("Ignoring stale cover failure for job_id=%s", result.job_id)
            return []
        hub = _HubDelivery(plan_id=await plan.get_plan_id_for_item(ws, plan_item_id))
        if not await plan.is_manual_cover_job(ws, result.job_id):
            return [hub]
        title = (await plan.get_item(ws, plan_item_id)).title
        text = job_failure_text(result.job_type, title=title)
        return [_ErrorDelivery(text=text, job_id=result.job_id), hub]
    return [_ErrorDelivery(text=job_failure_text(result.job_type), job_id=result.job_id)]


async def _apply_result(plan: Plan, article: Article, result: JobResult) -> list[_Delivery]:
    """The DB half of notification handling (#73): applies a finished Job's result to
    domain state - in the Job's own Пространство (ADR-0015) - and returns what still needs
    delivering to Telegram - nothing for a stale result. Pure DB writes only; no Telegram call
    happens here, so retrying this half alone (as a redelivered notification does) is exactly
    as idempotent as the domain operations it calls."""
    if result.status == "failed":
        return await _apply_failure(plan, article, result)

    ws = result.workspace_id
    output = result.output or {}
    if result.job_type == "generate_plan":
        topics = [
            TopicDraft(
                title=t["title"], summary=t.get("summary", ""), keywords=t.get("keywords", [])
            )
            for t in output["topics"]
        ]
        if not topics:
            # #84: an empty result creates no Plan (one with only "Утвердить всё" on it would
            # start nothing) - the owner gets what happened and what to do instead.
            return [
                _NoticeDelivery(
                    text=_empty_plan_text(output["week_label"], output.get("empty_reason"))
                )
            ]
        plan_id = await plan.add_topics(ws, output["week_label"], topics)
        return [_PlanDelivery(plan_id=plan_id)]
    if result.job_type == "regenerate_topic":
        plan_item_id = PlanItemId(output["plan_item_id"])
        await plan.apply_regeneration(
            ws,
            plan_item_id,
            TopicDraft(
                title=output["title"], summary=output["summary"], keywords=output["keywords"]
            ),
        )
        # #81: the new title shows up in the Plan message itself, not in a separate notice.
        return [_PlanDelivery(plan_id=await plan.get_plan_id_for_item(ws, plan_item_id))]
    if result.job_type in ("generate_article", "regenerate_article"):
        article_id = ArticleId(output["article_id"])
        application = await article.record_version(
            ws,
            article_id,
            GeneratedVersion(
                content=output["content"],
                prompt=output["prompt"],
                model=output["model"],
                tokens=output["tokens"],
                cost=output["cost"],
                source_job_id=result.job_id,
                research_status=output.get("research_status"),
            ),
        )
        if application == "stale":
            logger.info("Ignoring stale Article result for job_id=%s", result.job_id)
            return []
        hub = _HubDelivery(plan_id=await article.get_plan_id(ws, article_id))
        if result.job_type == "generate_article":
            return [_PagePublishDelivery(article_id=article_id), hub]
        return [_ArticleDelivery(article_id=article_id), hub]
    if result.job_type == "generate_cover":
        plan_item_id = PlanItemId(output["plan_item_id"])
        image = base64.b64decode(output["image"])
        await plan.apply_cover(ws, plan_item_id, image, output["mime_type"])
        hub = _HubDelivery(plan_id=await plan.get_plan_id_for_item(ws, plan_item_id))
        if not await plan.is_manual_cover_job(ws, result.job_id):
            return [hub]
        cover = _CoverDelivery(
            plan_item_id=plan_item_id, image=image, mime_type=output["mime_type"]
        )
        return [cover, hub]

    logger.warning("No notification renderer for job_type=%r", result.job_type)
    return []


async def _deliver(
    plan: Plan,
    article: Article,
    gateway: TelegramGateway,
    workspace_id: WorkspaceId,
    chat_id: int,
    delivery: _Delivery,
    publisher: ArticlePagePublisher | None = None,
) -> None:
    """The Telegram half of notification handling (#73): turns an `_apply_result` outcome
    into the actual message in the Workspace owner's chat. A Plan delivery goes through
    `deliver_plan_message`, a Хаб one through `deliver_plan_hub` - both send only the first time
    and edit the Plan's canonical message every time after, the fix for #73's duplicate-message
    gap."""
    if isinstance(delivery, _PlanDelivery):
        view = await plan.get(workspace_id, delivery.plan_id)
        await deliver_plan_message(plan, gateway, workspace_id, chat_id, view)
    elif isinstance(delivery, _HubDelivery):
        await deliver_plan_hub(plan, gateway, workspace_id, chat_id, delivery.plan_id)
    elif isinstance(delivery, _NoticeDelivery):
        await gateway.send_notice(chat_id, delivery.text)
    elif isinstance(delivery, _ArticleDelivery):
        view = await article.get(workspace_id, delivery.article_id)
        await send_article_card(gateway, workspace_id, chat_id, view, publisher)
    elif isinstance(delivery, _PagePublishDelivery):
        if publisher is not None:
            await publisher.publish(
                workspace_id, await article.get(workspace_id, delivery.article_id)
            )
    elif isinstance(delivery, _CoverDelivery):
        item = await plan.get_item(workspace_id, delivery.plan_item_id)
        await gateway.send_cover(chat_id, delivery.image, delivery.mime_type, item.title)
    elif isinstance(delivery, _ErrorDelivery):
        await gateway.send_error_with_retry(chat_id, delivery.text, delivery.job_id)


def _make_notification_handler(
    plan: Plan,
    article: Article,
    gateway: TelegramGateway,
    workspaces: WorkspaceDirectory,
    *,
    publisher: ArticlePagePublisher | None = None,
):
    async def handle(result: JobResult) -> None:
        # The Job's own Пространство and its owner's private chat - never the chat of whoever
        # wrote last (ADR-0015).
        workspace = await workspaces.get(result.workspace_id)
        if workspace is None:  # the composite FK on `jobs` makes this unreachable
            logger.error("Job %s has no Workspace %s", result.job_id, result.workspace_id)
            return
        deliveries = await _apply_result(plan, article, result)
        try:
            for delivery in deliveries:
                await _deliver(
                    plan,
                    article,
                    gateway,
                    workspace.id,
                    workspace.owner_telegram_id,
                    delivery,
                    publisher,
                )
        except TelegramForbiddenError:
            # The owner blocked the bot: delivered as far as it goes, no 5 retries, and the
            # Расписание tick leaves the Workspace alone until its next /start.
            logger.info("Workspace %s blocked the bot", workspace.id)
            await workspaces.mark_blocked(workspace.id)

    return handle


async def main(settings: Settings | None = None) -> None:
    logging.basicConfig(level=logging.INFO)
    settings = settings or load_settings()

    pool = await asyncpg.create_pool(dsn=settings.postgres_dsn)
    assert pool is not None
    try:
        await run_migrations(pool)

        queue = JobQueue(pool)
        workspaces = Workspaces(pool)
        schedule_settings = ScheduleSettings(pool)
        settings_store = WorkspaceSettingsStore(pool)
        settings_service = SettingsService(settings_store)
        bot_state = BotStateStore(pool)
        plan = Plan(pool, queue, covers_enabled=settings.covers_enabled)
        article = Article(pool, queue)

        bot = Bot(token=settings.telegram_bot_token)
        bot_client = _AiogramBotClient(bot)
        gateway = TelegramGateway(bot_client, covers=settings.covers_enabled)
        pending_inputs = PendingInputs(pool)
        plan_review = PlanReview(plan, TelegramCommentPrompt(bot_client), pending_inputs)
        article_regeneration = CommentGatedRegeneration[ArticleId](
            article.request_regeneration,
            TelegramCommentPrompt(bot_client, action="regenerate_article"),
            pending_inputs,
            kind="article_comment",
        )
        bot_username = (await bot.me()).username
        # The Инструкция's pictures and the banners (#114), uploaded once and reused by file_id.
        photos = AssetPhotos(bot_client, bot_state)
        guide = Guide(photos, bot_client)
        prompts = InputPrompt(bot_client, pending_inputs)

        # Its first run, right away, also catches up a deadline missed while the bot was down.
        scheduler = AsyncIOScheduler()
        schedule_weekly_plan_tick(scheduler, pool, plan)
        scheduler.start()

        settings_screen = SettingsScreen(
            settings_service, schedule_settings, workspaces, bot_client, prompts
        )
        main_menu = MainMenu(
            plan, queue, schedule_settings, bot_client, gateway, prompts, photos=photos
        )
        # Everyone's list (#95); a Глобальный администратор's own chat also lists /admin.
        await bot.set_my_commands(MENU_COMMANDS, scope=BotCommandScopeDefault())
        await sync_admin_commands(bot_client, settings.global_admin_telegram_ids)
        # What a new user sees before /start (#96): the empty-chat card and the profile.
        await bot.set_my_description(BOT_DESCRIPTION)
        await bot.set_my_short_description(BOT_SHORT_DESCRIPTION)
        onboarding = Onboarding(
            OnboardingState(settings_store),
            settings_service,
            prompts,
            bot_client,
            main_menu,
            schedule_settings,
            photos=photos,
        )

        telegraph_client = HttpxTelegraphClient()
        publisher = TelegraphPublisher(
            telegraph_client,
            article,
            bot_state,
            access_token=settings.telegraph_access_token,
            footer_link=project_footer(settings_store),
        )
        dispatcher = Dispatcher()
        dispatcher.include_router(
            _build_router(
                workspaces,
                plan,
                article,
                gateway,
                bot_client,
                plan_review,
                article_regeneration,
                settings_screen,
                main_menu,
                prompts,
                queue,
                settings,
                onboarding,
                guide,
                publisher=publisher,
                bot_username=bot_username,
            )
        )

        stop = asyncio.Event()
        register_shutdown(stop)

        notify_handler = _make_notification_handler(
            plan, article, gateway, workspaces, publisher=publisher
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
            await telegraph_client.aclose()
            await bot.session.close()
    finally:
        await pool.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
