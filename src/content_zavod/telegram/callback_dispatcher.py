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

from dataclasses import dataclass
from typing import Protocol, assert_never

from aiogram.types import CallbackQuery

from ..access import AccessError, Membership, Role, require_role
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
from .gateway import BotClient, TelegramGateway
from .generate_plan_command import handle_cancel_regenerate_plan, handle_confirm_regenerate_plan
from .history_command import (
    handle_history_page,
    handle_history_version,
    handle_history_versions,
    handle_history_week,
)
from .join_request_flow import JoinRequestFlow
from .persona_command import handle_persona_template_callback
from .plan_review import PlanReview
from .types import ArticleId, PlanId, PlanItemId

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
            await answer()
            await self._join_request_flow.request_access(
                callback_input.user_id, callback_input.username
            )
            await self._gateway.edit_notice(
                callback_input.chat_id,
                callback_input.message_id,
                "Заявка отправлена. Ожидайте одобрения владельца.",
            )
            return

        role = await self._membership.role_for(callback_input.user_id)
        deny_text = ACCESS_DENIED_TEXT if role is None else OWNER_ONLY_TEXT

        try:
            await self._dispatch_gated(callback_input, payload, role, deny_text, answer)
        except (DomainError, AccessError) as exc:
            await answer(str(exc), show_alert=True)

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
                await answer()
                await self._membership.remove_member(int(id_))
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
                will_enqueue = self._article_regeneration.has_matching_pending(
                    chat_id, user_id, ArticleId(id_)
                )
                await answer("Принято, генерирую..." if will_enqueue else None)
                if will_enqueue:
                    await self._gateway.edit_notice(chat_id, message_id, "⏳ Генерирую...")
                await self._article_regeneration.request(chat_id, user_id, ArticleId(id_))
            case SimpleAction(action="approve", id_=id_):
                if not await self._authorized("approve", role, deny_text, answer):
                    return
                await answer()
                # Accepting a ready Статья: no comment-wait, just the transition to "exported".
                await self._article.mark_exported(ArticleId(id_))
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
                will_enqueue = self._plan_review.will_enqueue_regeneration(
                    chat_id, user_id, plan_item_id
                )
                await answer("Принято, генерирую..." if will_enqueue else None)
                if will_enqueue:
                    await self._gateway.edit_notice(chat_id, message_id, "⏳ Генерирую...")
                await self._plan_review.handle_action(chat_id, user_id, plan_item_id, "regenerate")
            case SimpleAction(action="approve_all", id_=id_):
                if not await self._authorized("approve_all", role, deny_text, answer):
                    return
                await answer()
                await self._plan_review.handle_action(
                    chat_id, user_id, PlanItemId(id_), "approve_all"
                )
                await _generate_articles_for_approved_plan(self._plan, self._article, PlanId(id_))
            case SimpleAction(action="delete", id_=id_):
                if not await self._authorized("delete", role, deny_text, answer):
                    return
                await answer()
                await self._plan_review.handle_action(chat_id, user_id, PlanItemId(id_), "delete")
                await self._gateway.send_notice(chat_id, "Тема удалена.")
            case SimpleAction(action=unreachable):
                assert_never(unreachable)

    @staticmethod
    def _resolver_name(callback_input: CallbackInput) -> str:
        username = callback_input.username
        return f"@{username}" if username else str(callback_input.user_id)
