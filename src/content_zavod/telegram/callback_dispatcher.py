"""callback_dispatcher: the testable core of `on_callback` (candidate 04, ADR-0012, ADR-0015).

`CallbackInput` is the aiogram-free shape of an inbound callback; `unpack_callback_query`
is the one function that builds it from an aiogram `CallbackQuery`, so tests build
`CallbackInput` by hand instead of constructing aiogram objects. `CallbackDispatcher` stays
side-effecting - it holds `gateway`/`plan`/`article`/... the same way `on_callback` used to close
over them - and is one exhaustive `match` over the five composite payload shapes and
`SimpleAction.action`, with `assert_never` on any `Action` the match doesn't cover.

There are no Roles (ADR-0015): `dispatch` gets the `Workspace` of whoever pressed the button,
resolved by the bot from `from_user.id` - never from `callback_data`. Every branch that carries
an id (План, Тема, Статья, Версия, Задача) looks it up in that Workspace first, before it
answers the callback; «not found» - deleted, another Workspace's or never existed - is the same
alert, «Кнопка устарела», so the answer never hints that an id exists elsewhere. Telegram takes
only the first answer to a callback: a branch that answered before its lookup would leave the
alert unseen.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, assert_never

from aiogram.types import CallbackQuery

from ..domain import PLATFORMS, Article, DomainError, HubTopic, Plan
from ..job_queue import JobId, JobQueue, JobQueueError
from ..telegraph import page_url
from ..workspaces import Workspace, WorkspaceId
from .article_card import ArticlePagePublisher, send_article_card
from .callback_codec import (
    CallbackPayload,
    ExportArticle,
    HistoryVersion,
    HistoryVersions,
    HistoryWeek,
    Page,
    SimpleAction,
    decode_callback_data,
)
from .comment_gated_regeneration import CommentGatedRegeneration
from .gateway import ITEMS_PER_PAGE, BotClient, TelegramGateway
from .generate_plan_command import handle_cancel_regenerate_plan, handle_confirm_regenerate_plan
from .guide import FROM_GUIDE, FROM_MENU, Guide
from .history_command import (
    handle_history_page,
    handle_history_version,
    handle_history_versions,
    handle_history_week,
)
from .input_prompt import InputPrompt
from .main_menu import MainMenu
from .onboarding import ONBOARDING_INPUT_KIND, Onboarding
from .plan_review import PlanReview
from .settings_screen import BACK_TO_MENU, SettingsScreen
from .style_samples import STYLE_SAMPLES_KIND, StyleSamples
from .texts import (
    COVER_REQUESTED,
    COVERS_OFF,
    HUB_ALERT_NO_COVER,
    HUB_ALERT_NOTHING_TO_RETRY,
    HUB_ALERT_RETRYING,
    STALE_BUTTON,
    error_alert_text,
)
from .types import ArticleId, PlanId, PlanItemId

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CallbackInput:
    """`CallbackQuery`, unpacked to what the dispatcher actually needs."""

    chat_id: int
    message_id: int
    user_id: int
    username: str | None
    payload: CallbackPayload


class CallbackAnswerer(Protocol):
    """Shape of `aiogram.types.CallbackQuery.answer` - its bound method satisfies this
    directly, so no adapter is needed to pass it into `CallbackDispatcher.dispatch`."""

    async def __call__(self, text: str | None = None, show_alert: bool | None = None) -> object: ...


async def _generate_articles_for_approved_plan(
    plan: Plan, article: Article, workspace_id: WorkspaceId, plan_id: PlanId
) -> None:
    """Fan out each approved Тема into one Статья per Площадка and enqueue its `generate_article`
    Job (#14), plus one `generate_cover` Job per Тема (#15 - the whole week's content, including
    covers, is generated together, so no separate manual trigger is needed for the common case).
    Reads current DB state (`Plan.approved_items`) rather than acting only on items this call just
    approved, and both `Article.request_generation` and `Plan.request_cover` are themselves
    idempotent - so replaying this for an already-approved Plan (a retried `approve_all` callback,
    or a crash between approving and enqueueing) creates neither duplicate Статьи/обложки nor
    duplicate Jobs.

    Nothing here tracks progress (#91): the Хаб derives it from the rows these calls create."""
    for item in await plan.approved_items(workspace_id, plan_id):
        await plan.request_cover(workspace_id, item.id)
        for platform in PLATFORMS:
            await article.request_generation(
                workspace_id, plan_id, item.id, item.title, item.summary, item.keywords, platform
            )


def unpack_callback_query(callback: CallbackQuery) -> CallbackInput | None:
    """The one aiogram-facing function: `CallbackQuery -> CallbackInput`.

    Returns `None` for a query aiogram itself couldn't attribute to a user/message - the
    early `return` `on_callback` took before it ever reached dispatch logic. Raises
    `ValueError` (from `decode_callback_data`) for a callback_data aiogram delivered that
    this bot never encoded (or no longer does) - the caller answers with no text/alert.
    """
    if callback.from_user is None or callback.message is None:
        return None
    payload = decode_callback_data(callback.data or "")
    return CallbackInput(
        chat_id=callback.message.chat.id,
        message_id=callback.message.message_id,
        user_id=callback.from_user.id,
        username=callback.from_user.username,
        payload=payload,
    )


class CallbackDispatcher:
    """Everything the callback branches used to do inline in `on_callback`, now reachable
    without an aiogram `CallbackQuery` - tests call `dispatch` with a hand-built
    `CallbackInput` and a fake `answer`."""

    def __init__(
        self,
        plan: Plan,
        article: Article,
        gateway: TelegramGateway,
        bot_client: BotClient,
        plan_review: PlanReview,
        article_regeneration: CommentGatedRegeneration[ArticleId],
        settings_screen: SettingsScreen,
        queue: JobQueue,
        main_menu: MainMenu,
        prompts: InputPrompt,
        onboarding: Onboarding,
        *,
        guide: Guide,
        publisher: ArticlePagePublisher | None = None,
        style_samples: StyleSamples | None = None,
    ) -> None:
        self._style_samples = style_samples
        self._guide = guide
        self._plan = plan
        self._article = article
        self._gateway = gateway
        self._bot_client = bot_client
        self._plan_review = plan_review
        self._article_regeneration = article_regeneration
        self._settings_screen = settings_screen
        self._queue = queue
        self._main_menu = main_menu
        self._prompts = prompts
        self._onboarding = onboarding
        self._publisher = publisher

    async def dispatch(
        self, workspace: Workspace, callback_input: CallbackInput, answer: CallbackAnswerer
    ) -> None:
        try:
            await self._dispatch(workspace, callback_input, answer)
        except (DomainError, JobQueueError) as exc:
            # Russian alert by error class (#89): «Кнопка устарела» for any id this Пространство
            # doesn't have; the English technical text only goes to the log.
            logger.info("Callback %r refused: %s", callback_input.payload, exc)
            await answer(error_alert_text(exc), show_alert=True)

    async def _dispatch(
        self, workspace: Workspace, callback_input: CallbackInput, answer: CallbackAnswerer
    ) -> None:
        payload = callback_input.payload
        ws = workspace.id
        chat_id = callback_input.chat_id
        message_id = callback_input.message_id
        user_id = callback_input.user_id

        match payload:
            case SimpleAction(action="persona_template", id_=id_):
                await answer()
                # A Пресет button from a pre-#95 /persona message: same as picking it on the
                # Экран Настроек, which that message now turns into.
                await self._settings_screen.pick(
                    workspace, chat_id, message_id, "persona", int(id_)
                )
            case SimpleAction(action="menu", id_=id_):
                await answer()
                if id_ == FROM_GUIDE:
                    # «🏠 В меню» on the Инструкция: the menu takes the carousel's place (#114).
                    await self._bot_client.delete_message(chat_id, message_id)
                    await self._main_menu.send(chat_id)
                    return
                await self._main_menu.show(chat_id, message_id)
            case SimpleAction(action="guide", id_=id_):
                await answer()
                if id_ == FROM_MENU:  # the carousel takes the menu's place
                    await self._bot_client.delete_message(chat_id, message_id)
                await self._guide.send(chat_id)
            case SimpleAction(action="guide_slide", id_=id_):
                await answer()
                await self._guide.show(chat_id, message_id, id_)
            case SimpleAction(action="menu_plan"):
                await answer()
                await self._main_menu.show_plan(workspace, chat_id, message_id)
            case SimpleAction(action="menu_generate_plan"):
                await answer()
                await self._main_menu.generate_plan(workspace, chat_id)
            case SimpleAction(action="menu_topic"):
                await answer()
                await self._main_menu.ask_topic(workspace, chat_id, user_id)
            case SimpleAction(action="menu_history"):
                await answer()
                # In place of the menu; its «🏠 В меню» comes back.
                await handle_history_page(self._plan, self._gateway, ws, chat_id, message_id, 0)
            case SimpleAction(action="settings"):
                await answer()
                await self._settings_screen.show(workspace, chat_id, message_id)
            case SimpleAction(action="edit_setting", id_=id_):
                await answer()
                await self._settings_screen.edit(workspace, chat_id, user_id, message_id, id_)
            case SimpleAction(action="ask_setting", id_=id_):
                await answer()
                await self._settings_screen.ask(
                    workspace, chat_id, user_id, id_, screen_message_id=message_id
                )
            case SimpleAction(action="pick_setting", id_=id_):
                await answer()
                key, _, index = id_.partition(":")
                await self._settings_screen.pick(workspace, chat_id, message_id, key, int(index))
            case SimpleAction(action="schedule", id_=id_):
                await answer()
                await self._settings_screen.show_schedule(
                    workspace, chat_id, message_id, id_ or BACK_TO_MENU
                )
            case SimpleAction(action="schedule_day", id_=id_):
                await answer()
                day, _, back = id_.partition(":")
                await self._settings_screen.set_day(
                    workspace, chat_id, message_id, day, back or BACK_TO_MENU
                )
            case SimpleAction(action="schedule_time", id_=id_):
                await answer()
                await self._settings_screen.ask_time(
                    workspace, chat_id, user_id, message_id, id_ or BACK_TO_MENU
                )
            case SimpleAction(action="schedule_tz", id_=id_):
                await answer()
                await self._settings_screen.show_timezones(
                    workspace, chat_id, message_id, id_ or BACK_TO_MENU
                )
            case SimpleAction(action="schedule_tz_pick", id_=id_):
                await answer()
                index, _, back = id_.partition(":")
                await self._settings_screen.set_timezone(
                    workspace, chat_id, message_id, index, back or BACK_TO_MENU
                )
            case SimpleAction(action="cancel_input", id_=id_):
                await answer()
                # Drops only the presser's own wait asked by this very message. With none
                # (expired, already answered) the prompt is still theirs to clear.
                cancelled = await self._prompts.cancel(
                    ws, chat_id, user_id, id_, message_id=message_id
                )
                if not cancelled:
                    await self._bot_client.delete_message(chat_id, message_id)
                if cancelled and id_ == ONBOARDING_INPUT_KIND:
                    await self._onboarding.cancelled(chat_id)  # say how to come back (#96)
                if id_ == STYLE_SAMPLES_KIND and self._style_samples is not None:
                    await self._style_samples.drop(workspace, chat_id, user_id)
            case SimpleAction(
                action="style_start" | "style_done" | "style_presets" | "style_ok" | "style_fix"
            ) if self._style_samples is None:
                await answer(STALE_BUTTON, show_alert=True)  # a build without the flow
            case SimpleAction(action="style_start", id_=id_):
                await answer()
                await self._style().start(workspace, chat_id, user_id, id_)
            case SimpleAction(action="style_done"):
                alert = await self._style().done(workspace, chat_id, user_id, message_id)
                await answer(alert, show_alert=True if alert else None)
            case SimpleAction(action="style_presets"):
                await answer()
                # Collecting stops; the message itself becomes the Стиль автора chooser.
                await self._style().drop(workspace, chat_id, user_id)
                await self._settings_screen.edit(workspace, chat_id, user_id, message_id, "persona")
            case SimpleAction(action="style_ok", id_=id_):
                # Looked up in this Пространство first: a stale or foreign Job id saves nothing.
                if not await self._style().confirm(workspace, chat_id, message_id, id_):
                    await answer(STALE_BUTTON, show_alert=True)
                    return
                await answer()
            case SimpleAction(action="style_fix", id_=id_):
                if not await self._style().ask_fix(workspace, chat_id, user_id, id_):
                    await answer(STALE_BUTTON, show_alert=True)
                    return
                await answer()
            case SimpleAction(action="onboarding_step", id_=id_):
                await answer()
                await self._onboarding.go(workspace, chat_id, user_id, message_id, id_)
            case SimpleAction(action="onboarding_pick", id_=id_):
                await answer()
                await self._onboarding.pick(workspace, chat_id, user_id, message_id, id_)
            case SimpleAction(action="onboarding_later"):
                await answer()
                await self._onboarding.later(workspace, chat_id, message_id)
            case SimpleAction(action="onboarding_launch"):
                await answer()
                await self._onboarding.launch(workspace, chat_id, message_id)
            case Page(plan_id=plan_id, page=page):
                summary = await self._plan.get_summary(ws, PlanId(plan_id))
                await answer()
                # A stale page button pressed after approval redraws the Хаб, not the review list.
                if summary.status == "approved":
                    hub = await self._plan.get_hub(ws, PlanId(plan_id))
                    await self._gateway.edit_hub(chat_id, message_id, hub)
                    return
                view = await self._plan.get(ws, PlanId(plan_id))
                await self._gateway.edit_plan(chat_id, message_id, view, page=page)
            case SimpleAction(action="history_page", id_=id_):
                await answer()
                await handle_history_page(
                    self._plan, self._gateway, ws, chat_id, message_id, int(id_)
                )
            case HistoryWeek():
                await self._plan.get_summary(ws, PlanId(payload.plan_id))
                await answer()
                await handle_history_week(
                    self._plan, self._article, self._gateway, ws, chat_id, message_id, payload
                )
            case HistoryVersions():
                await self._article.get_summary(ws, ArticleId(payload.article_id))
                await answer()
                await handle_history_versions(
                    self._article, self._gateway, ws, chat_id, message_id, payload
                )
            case HistoryVersion():
                await self._article.get_version(
                    ws, ArticleId(payload.article_id), payload.version_id
                )
                await answer()
                await handle_history_version(
                    self._article, self._gateway, ws, chat_id, message_id, payload
                )
            case SimpleAction(action="confirm_regenerate_plan", id_=id_):
                await self._plan.get_summary(ws, PlanId(id_))
                await answer()
                await handle_confirm_regenerate_plan(
                    self._plan, self._gateway, ws, chat_id, message_id, PlanId(id_)
                )
            case SimpleAction(action="cancel_regenerate_plan", id_=id_):
                await self._plan.get_summary(ws, PlanId(id_))
                await answer()
                await handle_cancel_regenerate_plan(self._gateway, chat_id, message_id)
            case SimpleAction(action="retry", id_=id_):
                # «🔁 Повторить» under an old cover failure must not draw (and bill) a cover
                # after `YANDEX_COVERS=off` (#123).
                job = await self._queue.get_job(ws, JobId(int(id_)))
                if job is None:
                    await answer(STALE_BUTTON, show_alert=True)
                    return
                if not self._plan.covers_enabled and job.job_type == "generate_cover":
                    await answer(COVERS_OFF)
                    return
                await answer()
                await self._queue.retry(ws, JobId(int(id_)))
            case SimpleAction(action="regenerate_article", id_=id_):
                # Looked up first: the comment prompt must not open for a Статья this
                # Пространство doesn't have (ADR-0015).
                await self._article.get_summary(ws, ArticleId(id_))
                will_enqueue = await self._article_regeneration.has_matching_pending(
                    ws, chat_id, user_id, ArticleId(id_)
                )
                await answer("Принято, генерирую..." if will_enqueue else None)
                # The flow edits its own prompt message, never this card's (#80).
                await self._article_regeneration.request(ws, chat_id, user_id, ArticleId(id_))
            case SimpleAction(action="approve", id_=id_):
                # Accepting a ready Статья: no comment-wait, just the transition to "exported".
                # Transition first, so a refusal is the callback's only (alerting) answer; then
                # confirm it and redraw the card's ✅ as «✅ Готово» (#86).
                await self._article.mark_exported(ws, ArticleId(id_))
                await answer("Отмечено как готовое")
                view = await self._article.get(ws, ArticleId(id_))
                # Keep «📖 Читать» on the redrawn card (#92).
                path = await self._article.get_telegraph_path(ws, ArticleId(id_))
                await self._gateway.mark_article_card_exported(
                    chat_id, message_id, view, read_url=page_url(path) if path else None
                )
            case SimpleAction(action="request_cover", id_=id_):
                await self._plan.get_plan_id_for_item(ws, PlanItemId(id_))
                # A card sent while covers were on, pressed after `YANDEX_COVERS=off` (#123).
                if not self._plan.covers_enabled:
                    await answer(COVERS_OFF)
                    return
                await answer(COVER_REQUESTED)
                # By hand, from a Статья card: the result comes back here as a photo (#91).
                await self._plan.request_cover(ws, PlanItemId(id_), manual=True)
            case ExportArticle(article_id=article_id, article_format=article_format):
                view = await self._article.get(ws, ArticleId(article_id))
                await answer()
                await self._gateway.send_article_document(chat_id, view, article_format)
            case SimpleAction(action="regenerate", id_=id_):
                plan_item_id = PlanItemId(id_)
                # Looked up first: the comment prompt must not open for a Тема this
                # Пространство doesn't have (ADR-0015).
                await self._plan.get_plan_id_for_item(ws, plan_item_id)
                will_enqueue = await self._plan_review.will_enqueue_regeneration(
                    ws, chat_id, user_id, plan_item_id
                )
                await answer("Принято, генерирую..." if will_enqueue else None)
                # The flow edits its own prompt message, never the Plan's (#80).
                await self._plan_review.handle_action(ws, chat_id, user_id, id_, "regenerate")
            case SimpleAction(action="cancel_comment", id_=id_):
                await answer()
                # The Отмена button sits on the prompt itself (#88); the flow deletes the prompt
                # when it drops the wait. A stale one drops nothing and leaves the message as is:
                # only the presser's own wait is ever looked at, so no domain id is resolved.
                if not await self._plan_review.cancel_comment(
                    ws, chat_id, user_id, PlanItemId(id_)
                ):
                    await self._article_regeneration.cancel(ws, chat_id, user_id, ArticleId(id_))
            case SimpleAction(action="approve_all", id_=id_):
                await self._plan.get_summary(ws, PlanId(id_))
                await answer()
                await self._plan_review.handle_action(ws, chat_id, user_id, id_, "approve_all")
                await _generate_articles_for_approved_plan(
                    self._plan, self._article, ws, PlanId(id_)
                )
                # #81/#91: redrawn from the DB only once the fan-out is through, so a crash
                # mid-fan-out still leaves "Утвердить всё" there to replay it - and from now on
                # the Plan message is the Хаб.
                hub = await self._plan.get_hub(ws, PlanId(id_))
                await self._gateway.edit_hub(chat_id, message_id, hub)
            case SimpleAction(action="hub_topic", id_=id_):
                plan_id = await self._plan.get_plan_id_for_item(ws, PlanItemId(id_))
                await answer()
                await self._plan.set_hub_view(ws, plan_id, PlanItemId(id_))
                await self._redraw_hub(ws, chat_id, message_id, plan_id)
            case SimpleAction(action="hub_back", id_=id_):
                await self._plan.get_summary(ws, PlanId(id_))
                await answer()
                await self._plan.set_hub_view(ws, PlanId(id_), None)
                await self._redraw_hub(ws, chat_id, message_id, PlanId(id_))
            case SimpleAction(action="hub_cover", id_=id_):
                cover = await self._plan.get_cover(ws, PlanItemId(id_))
                if cover is None:
                    await answer(HUB_ALERT_NO_COVER if self._plan.covers_enabled else COVERS_OFF)
                    return
                await answer()
                await self._gateway.send_cover(chat_id, cover.image, cover.mime_type, cover.title)
            case SimpleAction(action="hub_article", id_=id_):
                view = await self._article.get(ws, ArticleId(id_))
                await answer()
                # The usual Статья card, as its own message: its ✏️/✅/.docx/.md keep working
                # exactly as before, and the Хаб stays put above it. The page was published
                # when the Статья became ready; only a missing one is published now (#92).
                path = await self._article.get_telegraph_path(ws, ArticleId(id_))
                if path is not None:
                    await self._gateway.send_article_ready(chat_id, view, read_url=page_url(path))
                else:
                    await send_article_card(self._gateway, ws, chat_id, view, self._publisher)
            case SimpleAction(action="hub_retry", id_=id_):
                hub = await self._plan.get_hub(ws, PlanId(id_))
                await self._retry_failed(ws, hub.topics, answer)
                await self._redraw_hub(ws, chat_id, message_id, hub.id)
            case SimpleAction(action="hub_retry_topic", id_=id_):
                plan_id = await self._plan.get_plan_id_for_item(ws, PlanItemId(id_))
                hub = await self._plan.get_hub(ws, plan_id)
                await self._retry_failed(ws, [t for t in hub.topics if t.id == id_], answer)
                await self._redraw_hub(ws, chat_id, message_id, plan_id)
            case SimpleAction(action="delete", id_=id_):
                plan_id = await self._plan.get_plan_id_for_item(ws, PlanItemId(id_))
                await answer()
                await self._plan_review.handle_action(ws, chat_id, user_id, id_, "delete")
                # #81: the Plan message itself shows the Тема as removed, no separate notice -
                # redrawn on the page that holds the Тема, so a press on page 2 stays there.
                view = await self._plan.get(ws, plan_id)
                index = next((i for i, item in enumerate(view.items) if item.id == id_), 0)
                await self._gateway.edit_plan(
                    chat_id, message_id, view, page=index // ITEMS_PER_PAGE
                )
            case SimpleAction(action=unreachable):
                assert_never(unreachable)

    def _style(self) -> StyleSamples:
        assert self._style_samples is not None  # the guarded `case` above answers otherwise
        return self._style_samples

    async def _redraw_hub(
        self, workspace_id: WorkspaceId, chat_id: int, message_id: int, plan_id: PlanId
    ) -> None:
        hub = await self._plan.get_hub(workspace_id, plan_id)
        await self._gateway.edit_hub(chat_id, message_id, hub)

    async def _retry_failed(
        self, workspace_id: WorkspaceId, topics: Sequence[HubTopic], answer: CallbackAnswerer
    ) -> None:
        """Re-runs every ❌ cell of `topics` (#91): a failed cover is requested afresh, a failed
        Статья's own Job is re-queued (the same thing the old «🔁 Повторить» message did). The
        Хаб then shows them ⏳ straight away - its state is read from those Jobs."""
        retried = 0
        for topic in topics:
            if topic.cover == "failed":
                await self._plan.request_cover(workspace_id, topic.id)
                retried += 1
            for cell in topic.articles:
                if cell.state == "failed" and cell.job_id is not None:
                    if await self._queue.retry(workspace_id, JobId(cell.job_id)):
                        retried += 1
        await answer(HUB_ALERT_RETRYING if retried else HUB_ALERT_NOTHING_TO_RETRY)
