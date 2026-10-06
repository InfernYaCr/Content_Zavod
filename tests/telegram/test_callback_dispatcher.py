"""Unit tests for `CallbackDispatcher` (candidate 04, ADR-0012) - built and driven entirely
through hand-built `CallbackInput` values and fakes, no aiogram `CallbackQuery` involved."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from aiogram.types import BufferedInputFile, InlineKeyboardMarkup

from content_zavod.access import MemberNotFound
from content_zavod.access.membership import MemberView, Role
from content_zavod.domain.plan import PlanItemDetail
from content_zavod.telegram import (
    ArticleId,
    ArticleSummary,
    ArticleVersionSummary,
    ArticleVersionView,
    ArticleView,
    CommentGatedRegeneration,
    ExportArticle,
    HistoryVersion,
    HistoryVersions,
    HistoryWeek,
    JoinRequestFlow,
    Page,
    PlanId,
    PlanItemId,
    PlanItemView,
    PlanReview,
    PlanSummary,
    PlanView,
    SimpleAction,
    TelegramGateway,
)
from content_zavod.telegram.callback_dispatcher import CallbackDispatcher, CallbackInput

OWNER_ID = 1
CM_ID = 2
UNKNOWN_ID = 3

_ACCESS_DENIED_TEXT = "Доступ запрещён. Обратитесь к владельцу бота, чтобы получить роль."
_OWNER_ONLY_TEXT = "Эта команда доступна только владельцу."


class FakeBot:
    """Satisfies `BotClient` so tests can wrap it in the real `TelegramGateway`."""

    def __init__(self) -> None:
        self.sent_messages: list[tuple[int, str, InlineKeyboardMarkup | None]] = []
        self.sent_documents: list[tuple[int, BufferedInputFile, str | None]] = []
        self.edited_messages: list[tuple[int, int, str, InlineKeyboardMarkup | None]] = []

    async def send_message(self, chat_id, text, reply_markup=None) -> int:
        self.sent_messages.append((chat_id, text, reply_markup))
        return len(self.sent_messages)

    async def send_document(self, chat_id, document, caption=None) -> None:
        self.sent_documents.append((chat_id, document, caption))

    async def send_photo(self, chat_id, photo, caption=None) -> None:
        pass

    async def edit_message_text(self, chat_id, message_id, text, reply_markup=None) -> None:
        self.edited_messages.append((chat_id, message_id, text, reply_markup))

    async def edit_message_reply_markup(self, chat_id, message_id, reply_markup=None) -> None:
        self.edited_messages.append((chat_id, message_id, "", reply_markup))

    async def set_my_commands(self, commands, *, scope) -> None:
        pass


class FakeMembership:
    """Satisfies both `CallbackDispatcher`'s own membership needs (`role_for`,
    `remove_member`) and `JoinRequestFlow`'s `MembershipOperations` (`list_by_role`,
    `add_member`) - one fake plays both roles, like the real `Membership` does."""

    def __init__(self, roles: dict[int, Role | None]) -> None:
        self._roles = roles
        self.removed: list[int] = []
        self.added: list[tuple[int, str]] = []

    async def role_for(self, telegram_id: int) -> Role | None:
        return self._roles.get(telegram_id)

    async def remove_member(self, telegram_id: int, *, removed_by: int) -> None:
        if telegram_id not in self._roles:
            raise MemberNotFound(telegram_id)
        self.removed.append(telegram_id)
        del self._roles[telegram_id]

    async def list_all(self) -> list[MemberView]:
        return [
            MemberView(telegram_id=tid, role=r, username=f"user{tid}")
            for tid, r in sorted(self._roles.items())
            if r is not None
        ]

    async def list_by_role(self, role: str) -> list[int]:
        return [tid for tid, r in self._roles.items() if r == role]

    async def add_member(self, telegram_id: int, role: str) -> None:
        self.added.append((telegram_id, role))
        self._roles[telegram_id] = role


class FakePlan:
    def __init__(self, *, start_generation_batch_returns: bool = True) -> None:
        self.cover_requests: list[PlanItemId] = []
        self.replacement_requests: list[PlanId] = []
        self.started_batches: list[tuple[PlanId, int]] = []
        self.recorded_progress_refs: list[tuple[PlanId, int, int]] = []
        self._start_generation_batch_returns = start_generation_batch_returns
        self._view = PlanView(
            id=PlanId("plan-1"),
            week_label="2026-W33",
            items=[PlanItemView(id=PlanItemId("item-1"), title="Тема", status="draft")],
        )
        self._summary = PlanSummary(
            id=PlanId("plan-1"), week_label="2026-W33", status="pending_review"
        )
        self._approved_items = [
            PlanItemDetail(id=PlanItemId("item-1"), title="Тема", summary="s", keywords=["k"])
        ]

    async def get(self, plan_id: PlanId) -> PlanView:
        return self._view

    async def get_summary(self, plan_id: PlanId) -> PlanSummary:
        return self._summary

    async def list_page(self, *, page: int, page_size: int) -> tuple[list[PlanSummary], int]:
        return [self._summary], 1

    async def request_cover(self, plan_item_id: PlanItemId) -> None:
        self.cover_requests.append(plan_item_id)

    async def approved_items(self, plan_id: PlanId) -> list[PlanItemDetail]:
        return self._approved_items

    async def request_replacement(self, plan_id: PlanId) -> None:
        self.replacement_requests.append(plan_id)

    async def start_generation_batch(self, plan_id: PlanId, total: int) -> bool:
        self.started_batches.append((plan_id, total))
        return self._start_generation_batch_returns

    async def record_progress_message_ref(
        self, plan_id: PlanId, chat_id: int, message_id: int
    ) -> None:
        self.recorded_progress_refs.append((plan_id, chat_id, message_id))


class FakeArticle:
    def __init__(self) -> None:
        self.mark_exported_calls: list[ArticleId] = []
        self.requested_generations: list[tuple] = []
        self._view = ArticleView(
            id=ArticleId("article-1"),
            plan_item_id=PlanItemId("item-1"),
            title="Статья",
            platform="tg",
            content=b"Hello",
        )
        self._summary = ArticleSummary(
            id=ArticleId("article-1"), title="Статья", platform="tg", status="ready"
        )
        self._version = ArticleVersionView(
            id=1, content="Hello", model="m", tokens=1, cost=0.1, created_at=datetime.now(UTC)
        )

    async def get(self, article_id: ArticleId) -> ArticleView:
        return self._view

    async def mark_exported(self, article_id: ArticleId) -> None:
        self.mark_exported_calls.append(article_id)

    async def request_generation(
        self, plan_id, plan_item_id, title, summary, keywords, platform
    ) -> str:
        self.requested_generations.append(
            (plan_id, plan_item_id, title, summary, keywords, platform)
        )
        return "job-1"

    async def list_summary_for_plan(self, plan_id: PlanId) -> list[ArticleSummary]:
        return [self._summary]

    async def get_summary(self, article_id: ArticleId) -> ArticleSummary:
        return self._summary

    async def get_plan_id(self, article_id: ArticleId) -> PlanId:
        return PlanId("plan-1")

    async def list_versions(self, article_id: ArticleId) -> list[ArticleVersionSummary]:
        return [
            ArticleVersionSummary(id=1, model="m", tokens=1, cost=0.1, created_at=datetime.now(UTC))
        ]

    async def get_version(self, article_id: ArticleId, version_id: int) -> ArticleVersionView:
        return self._version


class FakePlanOps:
    def __init__(self) -> None:
        self.deleted: list[PlanItemId] = []
        self.regenerated: list[tuple[PlanItemId, str | None]] = []
        self.approved: list[PlanItemId] = []

    async def delete_item(self, plan_item_id: PlanItemId) -> None:
        self.deleted.append(plan_item_id)

    async def regenerate_item(self, plan_item_id: PlanItemId, comment: str | None) -> None:
        self.regenerated.append((plan_item_id, comment))

    async def approve_all(self, plan_item_id: PlanItemId) -> None:
        self.approved.append(plan_item_id)


class FakePrompt:
    def __init__(self) -> None:
        self.prompted: list[tuple[int, object]] = []

    async def prompt_for_comment(self, chat_id: int, id_: object) -> None:
        self.prompted.append((chat_id, id_))


class FakeArticleRegen:
    def __init__(self) -> None:
        self.regenerated: list[tuple[ArticleId, str | None]] = []

    async def __call__(self, article_id: ArticleId, comment: str | None) -> None:
        self.regenerated.append((article_id, comment))


class FakeJoinRequests:
    def __init__(self) -> None:
        self._next_id = 1
        self._requests: dict[int, object] = {}
        self._broadcasts: dict[int, list[object]] = {}

    async def create(self, telegram_id: int, username: str | None) -> int | None:
        from content_zavod.access import JoinRequestView

        if any(
            r.telegram_id == telegram_id and r.status == "pending" for r in self._requests.values()
        ):
            return None
        request_id = self._next_id
        self._next_id += 1
        self._requests[request_id] = JoinRequestView(
            id=request_id,
            telegram_id=telegram_id,
            username=username,
            status="pending",
            resolved_by=None,
        )
        self._broadcasts[request_id] = []
        return request_id

    async def get(self, join_request_id: int):
        return self._requests[join_request_id]

    async def record_broadcast(
        self, join_request_id: int, owner_telegram_id: int, chat_id: int, message_id: int
    ) -> None:
        from content_zavod.access import JoinRequestBroadcast

        self._broadcasts[join_request_id].append(
            JoinRequestBroadcast(
                owner_telegram_id=owner_telegram_id, chat_id=chat_id, message_id=message_id
            )
        )

    async def broadcasts_for(self, join_request_id: int) -> list[object]:
        return self._broadcasts[join_request_id]

    async def resolve(self, join_request_id: int, *, approved: bool, resolved_by: int):
        from content_zavod.access import JoinRequestView

        current = self._requests[join_request_id]
        updated = JoinRequestView(
            id=current.id,
            telegram_id=current.telegram_id,
            username=current.username,
            status="approved" if approved else "declined",
            resolved_by=resolved_by,
            resolved_now=True,
        )
        self._requests[join_request_id] = updated
        return updated


class FakePersonaSettings:
    def __init__(self) -> None:
        self.set_calls: list[str] = []

    async def read(self):
        raise NotImplementedError

    async def set_persona(self, value: str) -> str:
        self.set_calls.append(value)
        return value


class FakeQueue:
    def __init__(self) -> None:
        self.retried: list[int] = []

    async def retry(self, job_id: int) -> bool:
        self.retried.append(job_id)
        return True


class FakeAnswerer:
    def __init__(self) -> None:
        self.calls: list[tuple[str | None, bool | None]] = []

    async def __call__(self, text: str | None = None, show_alert: bool | None = None) -> None:
        self.calls.append((text, show_alert))


class Fixtures:
    def __init__(self, *, plan: FakePlan | None = None) -> None:
        self.bot = FakeBot()
        self.gateway = TelegramGateway(self.bot)
        self.membership = FakeMembership({OWNER_ID: "owner", CM_ID: "content_manager"})
        self.plan = plan if plan is not None else FakePlan()
        self.article = FakeArticle()
        self.plan_ops = FakePlanOps()
        self.plan_review = PlanReview(self.plan_ops, FakePrompt())
        self.article_regen_op = FakeArticleRegen()
        self.article_regeneration = CommentGatedRegeneration[ArticleId](
            self.article_regen_op, FakePrompt()
        )
        self.join_requests = FakeJoinRequests()
        self.join_request_flow = JoinRequestFlow(self.join_requests, self.membership, self.gateway)
        self.persona_settings = FakePersonaSettings()
        self.queue = FakeQueue()
        self.dispatcher = CallbackDispatcher(
            self.membership,
            self.plan,
            self.article,
            self.gateway,
            self.bot,
            self.plan_review,
            self.article_regeneration,
            self.join_request_flow,
            self.persona_settings,
            self.queue,
        )


@pytest.fixture
def f() -> Fixtures:
    return Fixtures()


def make_input(payload, *, user_id: int = CM_ID, username: str | None = "cm") -> CallbackInput:
    return CallbackInput(
        chat_id=1, message_id=2, user_id=user_id, username=username, payload=payload
    )


async def dispatch(f: Fixtures, payload, *, user_id: int = CM_ID) -> FakeAnswerer:
    answer = FakeAnswerer()
    await f.dispatcher.dispatch(make_input(payload, user_id=user_id), answer)
    return answer


# --- request_access: handled before Role resolution, works for unregistered callers ---


async def test_request_access_works_for_unregistered_caller(f: Fixtures) -> None:
    answer = await dispatch(f, SimpleAction("request_access", "ignored"), user_id=UNKNOWN_ID)

    assert answer.calls == [(None, None)]
    request = await f.join_requests.get(1)
    assert request.telegram_id == UNKNOWN_ID
    assert f.bot.edited_messages[-1][2] == "Заявка отправлена. Ожидайте одобрения владельца."


async def test_repeated_request_access_says_already_sent_and_does_not_rebroadcast(
    f: Fixtures,
) -> None:
    """#90: the second tap while a заявка is pending isn't re-sent to the Owner."""
    await dispatch(f, SimpleAction("request_access", "ignored"), user_id=UNKNOWN_ID)
    sent_after_first = len(f.bot.sent_messages)

    await dispatch(f, SimpleAction("request_access", "ignored"), user_id=UNKNOWN_ID)

    assert len(f.bot.sent_messages) == sent_after_first
    assert f.bot.edited_messages[-1][2] == "Заявка уже отправлена. Ожидайте одобрения владельца."


async def test_request_access_by_an_existing_member_creates_no_request(f: Fixtures) -> None:
    """#90: approving a member's own заявка would demote them to content_manager - possibly
    the last Владелец - so a member's tap creates nothing."""
    answer = await dispatch(f, SimpleAction("request_access", "ignored"), user_id=OWNER_ID)

    assert answer.calls == [("У вас уже есть доступ.", None)]
    assert f.join_requests._requests == {}
    assert f.bot.sent_messages == []
    assert f.bot.edited_messages == []


# --- unregistered caller denied on every other Action ---


async def test_unregistered_caller_denied_with_access_denied_text(f: Fixtures) -> None:
    answer = await dispatch(f, SimpleAction("delete", "item-1"), user_id=UNKNOWN_ID)

    assert answer.calls == [(_ACCESS_DENIED_TEXT, True)]
    assert f.plan_ops.deleted == []


# --- owner-only Действия refuse content_manager, exactly the existing text/alert ---


async def test_approve_join_refuses_content_manager() -> None:
    f = Fixtures()
    answer = await dispatch(f, SimpleAction("approve_join", "1"))

    assert answer.calls == [(_OWNER_ONLY_TEXT, True)]
    assert f.membership.added == []


async def test_decline_join_refuses_content_manager(f: Fixtures) -> None:
    answer = await dispatch(f, SimpleAction("decline_join", "1"))

    assert answer.calls == [(_OWNER_ONLY_TEXT, True)]


async def test_remove_member_refuses_content_manager(f: Fixtures) -> None:
    answer = await dispatch(f, SimpleAction("remove_member", "42"))

    assert answer.calls == [(_OWNER_ONLY_TEXT, True)]
    assert f.membership.removed == []


async def test_persona_template_refuses_content_manager(f: Fixtures) -> None:
    answer = await dispatch(f, SimpleAction("persona_template", "0"))

    assert answer.calls == [(_OWNER_ONLY_TEXT, True)]
    assert f.persona_settings.set_calls == []


# --- owner-only Действия succeed for owner ---


async def test_approve_join_grants_content_manager_for_owner(f: Fixtures) -> None:
    await f.join_request_flow.request_access(100, "alice")

    answer = await dispatch(f, SimpleAction("approve_join", "1"), user_id=OWNER_ID)

    assert answer.calls == [(None, None)]
    assert f.membership.added == [(100, "content_manager")]


async def test_decline_join_resolves_without_granting(f: Fixtures) -> None:
    await f.join_request_flow.request_access(100, "alice")

    answer = await dispatch(f, SimpleAction("decline_join", "1"), user_id=OWNER_ID)

    assert answer.calls == [(None, None)]
    assert f.membership.added == []


def _button_labels(keyboard: InlineKeyboardMarkup) -> list[str]:
    return [button.text for row in keyboard.inline_keyboard for button in row]


async def test_remove_member_asks_for_confirmation_instead_of_removing(f: Fixtures) -> None:
    """#90: «Удалить» only swaps that member's row for «Да, удалить / Отмена»."""
    answer = await dispatch(f, SimpleAction("remove_member", str(CM_ID)), user_id=OWNER_ID)

    assert answer.calls == [(None, None)]
    assert f.membership.removed == []
    chat_id, message_id, text, keyboard = f.bot.edited_messages[-1]
    assert (chat_id, message_id) == (1, 2)
    assert "@user2 — Контент-менеджер" in text
    assert _button_labels(keyboard) == ["❌ Удалить @user1", "✅ Да, удалить @user2", "↩️ Отмена"]


async def test_remove_member_refuses_yourself_up_front(f: Fixtures) -> None:
    answer = await dispatch(f, SimpleAction("remove_member", str(OWNER_ID)), user_id=OWNER_ID)

    assert answer.calls == [("Нельзя удалить самого себя.", True)]
    assert f.bot.edited_messages == []


async def test_confirm_remove_member_removes_and_redraws_the_list(f: Fixtures) -> None:
    answer = await dispatch(f, SimpleAction("confirm_remove_member", str(CM_ID)), user_id=OWNER_ID)

    assert answer.calls == [("Участник удалён.", None)]
    assert f.membership.removed == [CM_ID]
    _, _, text, keyboard = f.bot.edited_messages[-1]
    assert "@user2" not in text
    assert _button_labels(keyboard) == ["❌ Удалить @user1"]


async def test_confirm_remove_member_refusal_is_the_only_answer(f: Fixtures) -> None:
    """#90: a refused removal (here: the last Владелец) alerts once and changes nothing."""
    from content_zavod.access import LastOwnerRemoval

    async def refuse(telegram_id: int, *, removed_by: int) -> None:
        raise LastOwnerRemoval()

    f.membership.remove_member = refuse  # type: ignore[method-assign]

    answer = await dispatch(f, SimpleAction("confirm_remove_member", "42"), user_id=OWNER_ID)

    assert answer.calls == [("Нельзя удалить последнего Владельца.", True)]
    assert f.bot.edited_messages == []


async def test_confirm_remove_member_already_removed_alerts_and_redraws(f: Fixtures) -> None:
    """#90: a stale «Да» (another Owner removed them first) alerts and replaces the stale
    confirmation row with the current list."""
    answer = await dispatch(f, SimpleAction("confirm_remove_member", "42"), user_id=OWNER_ID)

    assert answer.calls == [("Участник не найден.", True)]
    _, _, _, keyboard = f.bot.edited_messages[-1]
    assert _button_labels(keyboard) == ["❌ Удалить @user1", "❌ Удалить @user2"]


async def test_cancel_remove_member_redraws_the_plain_list(f: Fixtures) -> None:
    answer = await dispatch(f, SimpleAction("cancel_remove_member", str(CM_ID)), user_id=OWNER_ID)

    assert answer.calls == [(None, None)]
    assert f.membership.removed == []
    _, _, _, keyboard = f.bot.edited_messages[-1]
    assert _button_labels(keyboard) == ["❌ Удалить @user1", "❌ Удалить @user2"]


@pytest.mark.parametrize("action", ["confirm_remove_member", "cancel_remove_member"])
async def test_remove_member_confirmation_refuses_content_manager(f: Fixtures, action) -> None:
    answer = await dispatch(f, SimpleAction(action, "42"))

    assert answer.calls == [(_OWNER_ONLY_TEXT, True)]
    assert f.membership.removed == []


async def test_persona_template_sets_persona_for_owner(f: Fixtures) -> None:
    answer = await dispatch(f, SimpleAction("persona_template", "0"), user_id=OWNER_ID)

    assert answer.calls == [(None, None)]
    assert len(f.persona_settings.set_calls) == 1


# --- shared (any registered Role) Действия ---


async def test_page_edits_the_plan_view(f: Fixtures) -> None:
    answer = await dispatch(f, Page("plan-1", 1))

    assert answer.calls == [(None, None)]
    assert len(f.bot.edited_messages) == 1


async def test_history_page_edits_the_week_list(f: Fixtures) -> None:
    answer = await dispatch(f, SimpleAction("history_page", "1"))

    assert answer.calls == [(None, None)]
    assert len(f.bot.edited_messages) == 1


async def test_history_week_edits_the_article_list(f: Fixtures) -> None:
    answer = await dispatch(f, HistoryWeek("plan-1", 0))

    assert answer.calls == [(None, None)]
    assert len(f.bot.edited_messages) == 1


async def test_history_versions_edits_the_version_list(f: Fixtures) -> None:
    answer = await dispatch(f, HistoryVersions("article-1", 0))

    assert answer.calls == [(None, None)]
    assert len(f.bot.edited_messages) == 1


async def test_history_version_edits_the_version_detail(f: Fixtures) -> None:
    answer = await dispatch(f, HistoryVersion("article-1", 1, 0))

    assert answer.calls == [(None, None)]
    assert len(f.bot.edited_messages) == 1


async def test_confirm_regenerate_plan_requests_replacement(f: Fixtures) -> None:
    answer = await dispatch(f, SimpleAction("confirm_regenerate_plan", "plan-1"))

    assert answer.calls == [(None, None)]
    assert f.plan.replacement_requests == [PlanId("plan-1")]


async def test_cancel_regenerate_plan_just_edits_notice(f: Fixtures) -> None:
    answer = await dispatch(f, SimpleAction("cancel_regenerate_plan", "ignored"))

    assert answer.calls == [(None, None)]
    assert f.bot.edited_messages[-1][2] == "Отменено."


async def test_retry_requeues_the_job(f: Fixtures) -> None:
    answer = await dispatch(f, SimpleAction("retry", "7"))

    assert answer.calls == [(None, None)]
    assert f.queue.retried == [7]


async def test_regenerate_article_prompts_for_a_comment_on_first_press(f: Fixtures) -> None:
    answer = await dispatch(f, SimpleAction("regenerate_article", "article-1"))

    assert answer.calls == [(None, None)]
    assert len(f.article_regen_op.regenerated) == 0


async def test_approve_marks_article_exported(f: Fixtures) -> None:
    answer = await dispatch(f, SimpleAction("approve", "article-1"))

    assert answer.calls == [("Отмечено как готовое", None)]
    assert f.article.mark_exported_calls == [ArticleId("article-1")]


async def test_approve_redraws_the_card_keyboard_with_the_done_state(f: Fixtures) -> None:
    """#86: the card itself shows the Статья was accepted - ✅ becomes «✅ Готово»."""
    await dispatch(f, SimpleAction("approve", "article-1"))

    chat_id, message_id, _, keyboard = f.bot.edited_messages[-1]
    assert (chat_id, message_id) == (1, 2)
    labels = [button.text for row in keyboard.inline_keyboard for button in row]
    assert "✅ Готово" in labels
    assert "✅" not in labels


async def test_approve_refusal_is_the_only_answer(f: Fixtures) -> None:
    """A Статья that isn't ready gets one alerting answer, and the card isn't redrawn."""
    from content_zavod.domain import ArticleNotReady

    async def not_ready(article_id: ArticleId) -> None:
        raise ArticleNotReady(article_id)

    f.article.mark_exported = not_ready  # type: ignore[method-assign]

    answer = await dispatch(f, SimpleAction("approve", "article-1"))

    assert len(answer.calls) == 1
    assert answer.calls[0][1] is True
    assert f.bot.edited_messages == []


async def test_request_cover_requests_the_cover(f: Fixtures) -> None:
    answer = await dispatch(f, SimpleAction("request_cover", "item-1"))

    assert answer.calls == [("Генерирую обложку...", None)]
    assert f.plan.cover_requests == [PlanItemId("item-1")]


async def test_export_article_sends_the_document(f: Fixtures) -> None:
    answer = await dispatch(f, ExportArticle("article-1", "md"))

    assert answer.calls == [(None, None)]
    assert len(f.bot.sent_documents) == 1


async def test_regenerate_prompts_for_a_comment_on_first_press(f: Fixtures) -> None:
    answer = await dispatch(f, SimpleAction("regenerate", "item-1"))

    assert answer.calls == [(None, None)]
    assert f.plan_ops.regenerated == []


async def test_approve_all_approves_and_fans_out_generation(f: Fixtures) -> None:
    answer = await dispatch(f, SimpleAction("approve_all", "plan-1"))

    assert answer.calls == [(None, None)]
    assert f.plan_ops.approved == [PlanItemId("plan-1")]
    assert f.plan.cover_requests == [PlanItemId("item-1")]
    assert len(f.article.requested_generations) > 0


async def test_approve_all_starts_a_batch_sized_for_covers_and_articles(f: Fixtures) -> None:
    """#91: one Тема is 1 generate_cover + one generate_article per Площадка (2)."""
    await dispatch(f, SimpleAction("approve_all", "plan-1"))

    assert f.plan.started_batches == [(PlanId("plan-1"), 3)]


async def test_approve_all_sends_and_records_the_initial_progress_message(f: Fixtures) -> None:
    await dispatch(f, SimpleAction("approve_all", "plan-1"))

    assert f.bot.sent_messages[0] == (1, "🔄 Готовлю материалы: 0/3", None)
    assert f.plan.recorded_progress_refs == [(PlanId("plan-1"), 1, 1)]


async def test_approve_all_replay_does_not_resend_progress_message() -> None:
    """A replayed fan-out (retried callback) finds the batch already started -
    `start_generation_batch` returns False - so it must not send a second progress message."""
    f = Fixtures(plan=FakePlan(start_generation_batch_returns=False))

    await dispatch(f, SimpleAction("approve_all", "plan-1"))

    assert f.bot.sent_messages == []
    assert f.plan.recorded_progress_refs == []


async def test_delete_deletes_and_sends_notice(f: Fixtures) -> None:
    answer = await dispatch(f, SimpleAction("delete", "item-1"))

    assert answer.calls == [(None, None)]
    assert f.plan_ops.deleted == [PlanItemId("item-1")]
    assert f.bot.sent_messages[-1][1] == "Тема удалена."


# --- a DomainError/AccessError raised mid-branch is answered as a show_alert, not raised ---


async def test_domain_error_from_a_branch_is_answered_not_raised(f: Fixtures) -> None:
    from content_zavod.domain import DomainError

    async def boom(plan_item_id: PlanItemId) -> None:
        raise DomainError("some technical detail")

    f.plan_ops.delete_item = boom  # type: ignore[method-assign]

    answer = await dispatch(f, SimpleAction("delete", "item-1"))

    # The branch already answered() before calling the collaborator that raised (same
    # ordering as before this refactor); the DomainError produces a second, alerting answer -
    # an unmapped error class gets the generic Russian fallback, never its own text (#89).
    assert answer.calls[-1] == ("Не получилось, попробуйте ещё раз", True)


async def test_known_domain_error_is_alerted_in_russian_not_its_english_text(
    f: Fixtures,
) -> None:
    from content_zavod.domain.errors import PlanItemNotEditable

    async def boom(plan_item_id: PlanItemId) -> None:
        raise PlanItemNotEditable(plan_item_id, "approved")

    f.plan_ops.delete_item = boom  # type: ignore[method-assign]

    answer = await dispatch(f, SimpleAction("delete", "item-1"))

    assert answer.calls[-1] == ("Эту Тему уже нельзя изменить.", True)
