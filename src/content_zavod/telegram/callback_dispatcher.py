"""callback_dispatcher: the testable core of `on_callback` (candidate 04, ADR-0012).

`CallbackInput` is the aiogram-free shape of an inbound callback; `unpack_callback_query`
is the one function that builds it from an aiogram `CallbackQuery`, so tests build
`CallbackInput` by hand instead of constructing aiogram objects. `CallbackDispatcher` stays
side-effecting - it holds `gateway`/`membership`/`plan`/`article`/... the same way `on_callback`
used to close over them - and is one exhaustive `match` over the five composite payload shapes
and `SimpleAction.action`, with `assert_never` on any `Action` the match doesn't cover.

`request_access` works for unregistered callers, so it is handled before the Role is even
resolved and never reaches the match (`ACTION_ROLE` has no entry for it either - see
`callback_codec.py`). Every other of the twenty Действия calls `require_role(role,
ACTION_ROLE[action])` as the first thing its branch does, refusing via `answer(text,
show_alert=True)` without touching any collaborator when it fails - same text `gated()` uses
for command handlers in `entrypoints/bot.py`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol, assert_never

from aiogram.types import CallbackQuery

from ..access import AccessError, CannotRemoveSelf, MemberNotFound, Membership, Role, require_role
from ..domain import PLATFORMS, Article, DomainError, Plan
from ..job_queue import JobId, JobQueue
from ..settings import SettingsService
from .callback_codec import (
    ACTION_ROLE,
    Action,
    CallbackPayload,
    ExportArticle,
    HistoryVersion,
    HistoryVersions,
    HistoryWeek,
    Page,
    SimpleAction,
    decode_callback_data,
)
from .commands import sync_commands
from .comment_gated_regeneration import CommentGatedRegeneration
from .gateway import ITEMS_PER_PAGE, BotClient, TelegramGateway
from .generate_plan_command import handle_cancel_regenerate_plan, handle_confirm_regenerate_plan
from .history_command import (
    handle_history_page,
    handle_history_version,
    handle_history_versions,
    handle_history_week,
)
from .join_request_flow import JoinRequestFlow
from .members_command import redraw_members
from .persona_command import handle_persona_template_callback
from .plan_review import PlanReview
from .texts import error_alert_text
from .types import ArticleId, PlanId, PlanItemId

logger = logging.getLogger(__name__)

ACCESS_DENIED_TEXT = "Доступ запрещён. Обратитесь к владельцу бота, чтобы получить роль."
OWNER_ONLY_TEXT = "Эта команда доступна только владельцу."


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
    plan: Plan, article: Article, gateway: TelegramGateway, chat_id: int, plan_id: PlanId
) -> None:
    """Fan out each approved Тема into one Статья per Площадка and enqueue its `generate_article`
    Job (#14), plus one `generate_cover` Job per Тема (#15 - the whole week's content, including
    covers, is generated together, so no separate manual trigger is needed for the common case).
    Reads current DB state (`Plan.approved_items`) rather than acting only on items this call just
    approved, and both `Article.request_generation` and `Plan.request_cover` are themselves
    idempotent - so replaying this for an already-approved Plan (a retried `approve_all` callback,
    or a crash between approving and enqueueing) creates neither duplicate Статьи/обложки nor
    duplicate Jobs.

    Opens a progress batch sized for every Job this fan-out is about to enqueue (#91) and sends
    the one live-edited progress message the notification handler updates as each Job finishes,
    instead of one Telegram message per finished Job. `start_generation_batch` only returns
    `True` the one time it actually opens the batch, so a replay of this same fan-out (which
    creates no duplicate Jobs either, per above) doesn't send a second progress message."""
    items = await plan.approved_items(plan_id)
    total = len(items) * (1 + len(PLATFORMS))
    if total > 0 and await plan.start_generation_batch(plan_id, total):
        message_id = await gateway.send_generation_progress(chat_id, done=0, total=total)
        await plan.record_progress_message_ref(plan_id, chat_id, message_id)
    for item in items:
        await plan.request_cover(item.id)
        for platform in PLATFORMS:
            await article.request_generation(
                plan_id, item.id, item.title, item.summary, item.keywords, platform
            )


def unpack_callback_query(callback: CallbackQuery) -> CallbackInput | None:
    """The one aiogram-facing function: `CallbackQuery -> CallbackInput`.

    Returns `None` for a query aiogram itself couldn't attribute to a user/message - the
    early `return` `on_callback` took before it ever reached dispatch logic. Raises
    `ValueError` (from `decode_callback_data`) for a callback_data aiogram delivered that
    this bot never encoded - the caller answers with no text/alert, same as today.
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
    """Everything twenty callback branches used to do inline in `on_callback`, now reachable
    without an aiogram `CallbackQuery` - tests call `dispatch` with a hand-built
    `CallbackInput` and a fake `answer`."""

    def __init__(
        self,
        membership: Membership,
        plan: Plan,
        article: Article,
        gateway: TelegramGateway,
        bot_client: BotClient,
        plan_review: PlanReview,
        article_regeneration: CommentGatedRegeneration[ArticleId],
        join_request_flow: JoinRequestFlow,
        owner_settings_service: SettingsService,
        queue: JobQueue,
    ) -> None:
        self._membership = membership
        self._plan = plan
        self._article = article
        self._gateway = gateway
        self._bot_client = bot_client
        self._plan_review = plan_review
        self._article_regeneration = article_regeneration
        self._join_request_flow = join_request_flow
        self._owner_settings_service = owner_settings_service
        self._queue = queue

    async def dispatch(self, callback_input: CallbackInput, answer: CallbackAnswerer) -> None:
        payload = callback_input.payload
        if isinstance(payload, SimpleAction) and payload.action == "request_access":
            # An existing member's заявка would demote them on approval (`add_member` overwrites
            # the Role) - possibly the last Владелец (#90).
            if await self._membership.role_for(callback_input.user_id) is not None:
                await answer("У вас уже есть доступ.")
                return
            await answer()
            sent = await self._join_request_flow.request_access(
                callback_input.user_id, callback_input.username
            )
            await self._gateway.edit_notice(
                callback_input.chat_id,
                callback_input.message_id,
                "Заявка отправлена. Ожидайте одобрения владельца."
                if sent
                else "Заявка уже отправлена. Ожидайте одобрения владельца.",
            )
            return

        role = await self._membership.role_for(callback_input.user_id)
        deny_text = ACCESS_DENIED_TEXT if role is None else OWNER_ONLY_TEXT

        try:
            await self._dispatch_gated(callback_input, payload, role, deny_text, answer)
        except (DomainError, AccessError) as exc:
            # Russian alert by error class (#89); the English technical text only goes to the log.
            logger.info("Callback %r refused: %s", payload, exc)
            await answer(error_alert_text(exc), show_alert=True)

    async def _authorized(
        self, action: Action, role: Role | None, deny_text: str, answer: CallbackAnswerer
    ) -> bool:
        """First line of every branch below: True to proceed, False (already answered
        with `deny_text`, alerting) to skip the branch's collaborator entirely."""
        if require_role(role, ACTION_ROLE[action]):
            return True
        await answer(deny_text, show_alert=True)
        return False

    async def _dispatch_gated(
        self,
        callback_input: CallbackInput,
        payload: CallbackPayload,
        role: Role | None,
        deny_text: str,
        answer: CallbackAnswerer,
    ) -> None:
        chat_id = callback_input.chat_id
        message_id = callback_input.message_id
        user_id = callback_input.user_id

        match payload:
            case SimpleAction(action="approve_join", id_=id_):
                if not await self._authorized("approve_join", role, deny_text, answer):
                    return
                await answer()
                resolved = await self._join_request_flow.handle_approve(
                    user_id, self._resolver_name(callback_input), int(id_)
                )
                if resolved is not None and resolved.status == "approved":
                    await sync_commands(self._bot_client, resolved.telegram_id, "content_manager")
            case SimpleAction(action="decline_join", id_=id_):
                if not await self._authorized("decline_join", role, deny_text, answer):
                    return
                await answer()
                await self._join_request_flow.handle_decline(
                    user_id, self._resolver_name(callback_input), int(id_)
                )
            case SimpleAction(action="remove_member", id_=id_):
                if not await self._authorized("remove_member", role, deny_text, answer):
                    return
                if int(id_) == user_id:
                    raise CannotRemoveSelf()  # refuse up front rather than after «Да» (#90)
                await answer()
                # Ask first (#90): redraw the list with this member's row as «Да / Отмена».
                await redraw_members(
                    self._membership, self._bot_client, chat_id, message_id, confirm_id=int(id_)
                )
            case SimpleAction(action="confirm_remove_member", id_=id_):
                if not await self._authorized("confirm_remove_member", role, deny_text, answer):
                    return
                # Remove first, so a refusal (self / last Владелец) is the only, alerting answer.
                try:
                    await self._membership.remove_member(int(id_), removed_by=user_id)
                except MemberNotFound:
                    # Another Owner already removed them: replace the stale «Да / Отмена» row,
                    # then let the generic handler alert as usual.
                    await redraw_members(self._membership, self._bot_client, chat_id, message_id)
                    raise
                await answer("Участник удалён.")
                await redraw_members(self._membership, self._bot_client, chat_id, message_id)
            case SimpleAction(action="cancel_remove_member"):
                if not await self._authorized("cancel_remove_member", role, deny_text, answer):
                    return
                await answer()
                await redraw_members(self._membership, self._bot_client, chat_id, message_id)
            case SimpleAction(action="persona_template", id_=id_):
                if not await self._authorized("persona_template", role, deny_text, answer):
                    return
                await answer()
                await handle_persona_template_callback(
                    self._owner_settings_service, self._gateway, chat_id, int(id_)
                )
            case Page(plan_id=plan_id, page=page):
                if not await self._authorized("page", role, deny_text, answer):
                    return
                await answer()
                view = await self._plan.get(PlanId(plan_id))
                await self._gateway.edit_plan(chat_id, message_id, view, page=page)
            case SimpleAction(action="history_page", id_=id_):
                if not await self._authorized("history_page", role, deny_text, answer):
                    return
                await answer()
                await handle_history_page(self._plan, self._gateway, chat_id, message_id, int(id_))
            case HistoryWeek():
                if not await self._authorized("history_week", role, deny_text, answer):
                    return
                await answer()
                await handle_history_week(
                    self._plan, self._article, self._gateway, chat_id, message_id, payload
                )
            case HistoryVersions():
                if not await self._authorized("history_versions", role, deny_text, answer):
                    return
                await answer()
                await handle_history_versions(
                    self._article, self._gateway, chat_id, message_id, payload
                )
            case HistoryVersion():
                if not await self._authorized("history_version", role, deny_text, answer):
                    return
                await answer()
                await handle_history_version(
                    self._article, self._gateway, chat_id, message_id, payload
                )
            case SimpleAction(action="confirm_regenerate_plan", id_=id_):
                if not await self._authorized("confirm_regenerate_plan", role, deny_text, answer):
                    return
                await answer()
                await handle_confirm_regenerate_plan(
                    self._plan, self._gateway, chat_id, message_id, PlanId(id_)
                )
            case SimpleAction(action="cancel_regenerate_plan"):
                if not await self._authorized("cancel_regenerate_plan", role, deny_text, answer):
                    return
                await answer()
                await handle_cancel_regenerate_plan(self._gateway, chat_id, message_id)
            case SimpleAction(action="retry", id_=id_):
                if not await self._authorized("retry", role, deny_text, answer):
                    return
                await answer()
                await self._queue.retry(JobId(int(id_)))
            case SimpleAction(action="regenerate_article", id_=id_):
                if not await self._authorized("regenerate_article", role, deny_text, answer):
                    return
                will_enqueue = await self._article_regeneration.has_matching_pending(
                    chat_id, user_id, ArticleId(id_)
                )
                await answer("Принято, генерирую..." if will_enqueue else None)
                # The flow edits its own prompt message, never this card's (#80).
                await self._article_regeneration.request(chat_id, user_id, ArticleId(id_))
            case SimpleAction(action="approve", id_=id_):
                if not await self._authorized("approve", role, deny_text, answer):
                    return
                # Accepting a ready Статья: no comment-wait, just the transition to "exported".
                # Transition first, so a refusal is the callback's only (alerting) answer; then
                # confirm it and redraw the card's ✅ as «✅ Готово» (#86).
                await self._article.mark_exported(ArticleId(id_))
                await answer("Отмечено как готовое")
                view = await self._article.get(ArticleId(id_))
                await self._gateway.mark_article_card_exported(chat_id, message_id, view)
            case SimpleAction(action="request_cover", id_=id_):
                if not await self._authorized("request_cover", role, deny_text, answer):
                    return
                await answer("Генерирую обложку...")
                await self._plan.request_cover(PlanItemId(id_))
            case ExportArticle(article_id=article_id, article_format=article_format):
                if not await self._authorized("export_article", role, deny_text, answer):
                    return
                await answer()
                view = await self._article.get(ArticleId(article_id))
                await self._gateway.send_article_document(chat_id, view, article_format)
            case SimpleAction(action="regenerate", id_=id_):
                if not await self._authorized("regenerate", role, deny_text, answer):
                    return
                plan_item_id = PlanItemId(id_)
                will_enqueue = await self._plan_review.will_enqueue_regeneration(
                    chat_id, user_id, plan_item_id
                )
                await answer("Принято, генерирую..." if will_enqueue else None)
                # The flow edits its own prompt message, never the Plan's (#80).
                await self._plan_review.handle_action(chat_id, user_id, plan_item_id, "regenerate")
            case SimpleAction(action="cancel_comment", id_=id_):
                if not await self._authorized("cancel_comment", role, deny_text, answer):
                    return
                await answer()
                # The Отмена button sits on the prompt itself (#88); the flow deletes the prompt
                # when it drops the wait. A stale one, or someone else's, drops nothing and
                # leaves the message as is.
                if not await self._plan_review.cancel_comment(chat_id, user_id, PlanItemId(id_)):
                    await self._article_regeneration.cancel(chat_id, user_id, ArticleId(id_))
            case SimpleAction(action="approve_all", id_=id_):
                if not await self._authorized("approve_all", role, deny_text, answer):
                    return
                await answer()
                await self._plan_review.handle_action(
                    chat_id, user_id, PlanItemId(id_), "approve_all"
                )
                await _generate_articles_for_approved_plan(
                    self._plan, self._article, self._gateway, chat_id, PlanId(id_)
                )
                # #81: re-rendered from the DB only once the fan-out is through, so a crash
                # mid-fan-out still leaves "Утвердить всё" there to replay it.
                view = await self._plan.get(PlanId(id_))
                await self._gateway.edit_plan(chat_id, message_id, view)
            case SimpleAction(action="delete", id_=id_):
                if not await self._authorized("delete", role, deny_text, answer):
                    return
                await answer()
                await self._plan_review.handle_action(chat_id, user_id, PlanItemId(id_), "delete")
                # #81: the Plan message itself shows the Тема as removed, no separate notice -
                # redrawn on the page that holds the Тема, so a press on page 2 stays there.
                plan_id = await self._plan.get_plan_id_for_item(PlanItemId(id_))
                view = await self._plan.get(plan_id)
                index = next((i for i, item in enumerate(view.items) if item.id == id_), 0)
                await self._gateway.edit_plan(
                    chat_id, message_id, view, page=index // ITEMS_PER_PAGE
                )
            case SimpleAction(action=unreachable):
                assert_never(unreachable)

    @staticmethod
    def _resolver_name(callback_input: CallbackInput) -> str:
        username = callback_input.username
        return f"@{username}" if username else str(callback_input.user_id)
