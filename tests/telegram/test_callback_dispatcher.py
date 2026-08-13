"""Unit tests for `CallbackDispatcher` (candidate 04, ADR-0012) - built and driven entirely
through hand-built `CallbackInput` values and fakes, no aiogram `CallbackQuery` involved."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from aiogram.types import BufferedInputFile, InlineKeyboardMarkup

from content_zavod.access.membership import Role
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

    async def remove_member(self, telegram_id: int) -> None:
        self.removed.append(telegram_id)

    async def list_by_role(self, role: str) -> list[int]:
        return [tid for tid, r in self._roles.items() if r == role]

    async def add_member(self, telegram_id: int, role: str) -> None:
        self.added.append((telegram_id, role))
        self._roles[telegram_id] = role


class FakePlan:
    def __init__(self) -> None:
        self.cover_requests: list[PlanItemId] = []
        self.replacement_requests: list[PlanId] = []
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

    async def create(self, telegram_id: int, username: str | None) -> int:
        from content_zavod.access import JoinRequestView

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
    def __init__(self) -> None:
        self.bot = FakeBot()
        self.gateway = TelegramGateway(self.bot)
        self.membership = FakeMembership({OWNER_ID: "owner", CM_ID: "content_manager"})
        self.plan = FakePlan()
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


async def test_remove_member_removes_for_owner(f: Fixtures) -> None:
    answer = await dispatch(f, SimpleAction("remove_member", "42"), user_id=OWNER_ID)

    assert answer.calls == [(None, None)]
    assert f.membership.removed == [42]


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

    assert answer.calls == [(None, None)]
    assert f.article.mark_exported_calls == [ArticleId("article-1")]


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


async def test_delete_deletes_and_sends_notice(f: Fixtures) -> None:
    answer = await dispatch(f, SimpleAction("delete", "item-1"))

    assert answer.calls == [(None, None)]
    assert f.plan_ops.deleted == [PlanItemId("item-1")]
    assert f.bot.sent_messages[-1][1] == "Тема удалена."


# --- a DomainError/AccessError raised mid-branch is answered as a show_alert, not raised ---


async def test_domain_error_from_a_branch_is_answered_not_raised(f: Fixtures) -> None:
    from content_zavod.domain import DomainError

    async def boom(plan_item_id: PlanItemId) -> None:
        raise DomainError("Тема не найдена")

    f.plan_ops.delete_item = boom  # type: ignore[method-assign]

    answer = await dispatch(f, SimpleAction("delete", "item-1"))

    # The branch already answered() before calling the collaborator that raised (same
    # ordering as before this refactor); the DomainError produces a second, alerting answer.
    assert answer.calls[-1] == ("Тема не найдена", True)
