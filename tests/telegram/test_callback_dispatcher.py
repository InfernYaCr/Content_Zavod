"""Unit tests for `CallbackDispatcher` (candidate 04, ADR-0012) - built and driven entirely
through hand-built `CallbackInput` values and fakes, no aiogram `CallbackQuery` involved."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest
from aiogram.types import BufferedInputFile, InlineKeyboardMarkup

from content_zavod.access import MemberNotFound
from content_zavod.access.membership import MemberView, Role
from content_zavod.domain import HubArticleCell, HubTopic, PlanHubView, PlanItemCoverView
from content_zavod.domain.plan import PlanItemDetail
from content_zavod.scheduling import ScheduleConfig
from content_zavod.settings import OnboardingState, SettingsService
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
    PlanMessageRef,
    PlanReview,
    PlanSummary,
    PlanView,
    SimpleAction,
    TelegramGateway,
    decode_callback_data,
)
from content_zavod.telegram.callback_dispatcher import CallbackDispatcher, CallbackInput
from content_zavod.telegram.input_prompt import InputPrompt
from content_zavod.telegram.main_menu import MainMenu
from content_zavod.telegram.onboarding import ONBOARDING_INPUT_KIND, Onboarding
from content_zavod.telegram.pending_inputs import PendingInput
from content_zavod.telegram.settings_screen import SETTING_INPUT_KIND, SettingsScreen

from .fakes import FakePendingInputs

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
        self.deleted_messages: list[tuple[int, int]] = []
        self.sent_photos: list[tuple[int, BufferedInputFile, str | None]] = []

    async def send_message(self, chat_id, text, reply_markup=None, parse_mode=None) -> int:
        self.sent_messages.append((chat_id, text, reply_markup))
        return len(self.sent_messages)

    async def send_document(self, chat_id, document, caption=None) -> None:
        self.sent_documents.append((chat_id, document, caption))

    async def send_photo(self, chat_id, photo, caption=None) -> None:
        self.sent_photos.append((chat_id, photo, caption))

    async def edit_message_text(self, chat_id, message_id, text, reply_markup=None) -> None:
        self.edited_messages.append((chat_id, message_id, text, reply_markup))

    async def edit_message_reply_markup(self, chat_id, message_id, reply_markup=None) -> None:
        self.edited_messages.append((chat_id, message_id, "", reply_markup))

    async def delete_message(self, chat_id, message_id) -> None:
        self.deleted_messages.append((chat_id, message_id))

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
    def __init__(self) -> None:
        self.cover_requests: list[PlanItemId] = []
        self.manual_cover_requests: list[PlanItemId] = []
        self.replacement_requests: list[PlanId] = []
        self.hub_views: list[tuple[PlanId, PlanItemId | None]] = []
        self.cover: PlanItemCoverView | None = None
        self.hub = PlanHubView(
            id=PlanId("plan-1"),
            week_label="2026-W33",
            status="approved",
            topics=[
                HubTopic(
                    id=PlanItemId("item-1"),
                    number=1,
                    title="Тема",
                    cover="failed",
                    has_cover=False,
                    articles=[
                        HubArticleCell(
                            platform="zen",
                            state="ready",
                            article_id=ArticleId("article-1"),
                            has_content=True,
                            job_id=11,
                        ),
                        HubArticleCell(
                            platform="vc",
                            state="failed",
                            article_id=ArticleId("article-2"),
                            job_id=12,
                        ),
                    ],
                )
            ],
        )
        self.message_ref: PlanMessageRef | None = None
        self.requested_weeks: list[str] = []
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

    async def find_active(self, week_label: str) -> PlanView | None:
        return None

    async def request_new(self, week_label: str, *, generation_id: str | None = None) -> int:
        self.requested_weeks.append(week_label)
        return 1

    async def get_plan_id_for_item(self, plan_item_id: PlanItemId) -> PlanId:
        return self._view.id

    async def get_message_ref(self, plan_id: PlanId) -> PlanMessageRef | None:
        return self.message_ref

    async def get_summary(self, plan_id: PlanId) -> PlanSummary:
        return self._summary

    async def list_page(self, *, page: int, page_size: int) -> tuple[list[PlanSummary], int]:
        return [self._summary], 1

    async def request_cover(self, plan_item_id: PlanItemId, *, manual: bool = False) -> None:
        (self.manual_cover_requests if manual else self.cover_requests).append(plan_item_id)

    async def get_hub(self, plan_id: PlanId) -> PlanHubView:
        open_item = self.hub_views[-1][1] if self.hub_views else None
        return replace(self.hub, open_item_id=open_item)

    async def set_hub_view(self, plan_id: PlanId, plan_item_id: PlanItemId | None) -> None:
        self.hub_views.append((plan_id, plan_item_id))

    async def get_cover(self, plan_item_id: PlanItemId) -> PlanItemCoverView | None:
        return self.cover

    async def approved_items(self, plan_id: PlanId) -> list[PlanItemDetail]:
        return self._approved_items

    async def request_replacement(self, plan_id: PlanId) -> None:
        self.replacement_requests.append(plan_id)


class FakeArticle:
    def __init__(self) -> None:
        self.mark_exported_calls: list[ArticleId] = []
        self.telegraph_path: str | None = None
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

    async def get_telegraph_path(self, article_id: ArticleId) -> str | None:
        return self.telegraph_path

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
    """Each prompt is two messages, ids 100 (with the buttons) and 101 (ForceReply)."""

    def __init__(self) -> None:
        self.prompted: list[tuple[int, object]] = []
        self.generating: list[tuple[int, int]] = []
        self.withdrawn: list[tuple[int, int]] = []

    async def prompt_for_comment(
        self, chat_id: int, user_id: int, id_: object
    ) -> tuple[int, int | None]:
        self.prompted.append((chat_id, id_))
        return 100, 101

    async def mark_generating(self, chat_id: int, pending: PendingInput) -> None:
        self.generating.append((chat_id, pending.prompt_message_id))

    async def withdraw(self, chat_id: int, pending: PendingInput) -> None:
        self.withdrawn.append((chat_id, pending.prompt_message_id))


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


class FakeOwnerSettingsStore:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def set(self, key: str, value: str) -> None:
        self.values[key] = value

    async def set_if_changed(self, key: str, value: str) -> bool:
        changed = self.values.get(key) != value
        self.values[key] = value
        return changed


class FakeSchedule:
    def __init__(self) -> None:
        self.config: ScheduleConfig | None = None

    async def get(self) -> ScheduleConfig | None:
        return self.config

    async def set(self, day_of_week: str, hour: int, minute: int) -> None:
        self.config = ScheduleConfig(day_of_week, hour, minute)


class FakeScheduler:
    def __init__(self) -> None:
        self.rescheduled: list[str] = []

    def reschedule_job(self, job_id, *, trigger) -> None:
        self.rescheduled.append(job_id)


class FakeQueue:
    def __init__(self) -> None:
        self.retried: list[int] = []

    async def get_status(self, job_id: int) -> str:
        return "queued"

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
        self.pending_inputs = FakePendingInputs()
        self.plan_prompt = FakePrompt()
        self.plan_review = PlanReview(self.plan_ops, self.plan_prompt, self.pending_inputs)
        self.article_regen_op = FakeArticleRegen()
        self.article_prompt = FakePrompt()
        self.article_regeneration = CommentGatedRegeneration[ArticleId](
            self.article_regen_op, self.article_prompt, self.pending_inputs, kind="article_comment"
        )
        self.join_requests = FakeJoinRequests()
        self.join_request_flow = JoinRequestFlow(self.join_requests, self.membership, self.gateway)
        self.owner_settings = FakeOwnerSettingsStore()
        self.schedule = FakeSchedule()
        self.scheduler = FakeScheduler()
        self.queue = FakeQueue()
        self.prompts = InputPrompt(self.bot, self.pending_inputs)
        self.settings_screen = SettingsScreen(
            SettingsService(self.owner_settings),
            self.schedule,
            self.scheduler,
            self.bot,
            self.prompts,
            tz=ZoneInfo("Europe/Moscow"),
        )
        self.main_menu = MainMenu(
            self.plan,
            self.queue,
            self.schedule,
            self.bot,
            self.gateway,
            self.prompts,
            team_chat_id=-100500,
            tz=ZoneInfo("Europe/Moscow"),
        )
        self.onboarding = Onboarding(
            OnboardingState(self.owner_settings),
            SettingsService(self.owner_settings),
            self.prompts,
            self.bot,
            self.main_menu,
            self.schedule,
        )
        self.dispatcher = CallbackDispatcher(
            self.membership,
            self.plan,
            self.article,
            self.gateway,
            self.bot,
            self.plan_review,
            self.article_regeneration,
            self.join_request_flow,
            self.settings_screen,
            self.queue,
            self.main_menu,
            self.prompts,
            self.onboarding,
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
    assert f.owner_settings.values == {}


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
    assert _button_labels(keyboard) == [
        "❌ Удалить @user1",
        "✅ Да, удалить @user2",
        "↩️ Отмена",
        "🏠 В меню",
    ]


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
    assert _button_labels(keyboard) == ["❌ Удалить @user1", "🏠 В меню"]


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
    assert _button_labels(keyboard) == ["❌ Удалить @user1", "❌ Удалить @user2", "🏠 В меню"]


async def test_cancel_remove_member_redraws_the_plain_list(f: Fixtures) -> None:
    answer = await dispatch(f, SimpleAction("cancel_remove_member", str(CM_ID)), user_id=OWNER_ID)

    assert answer.calls == [(None, None)]
    assert f.membership.removed == []
    _, _, _, keyboard = f.bot.edited_messages[-1]
    assert _button_labels(keyboard) == ["❌ Удалить @user1", "❌ Удалить @user2", "🏠 В меню"]


@pytest.mark.parametrize("action", ["confirm_remove_member", "cancel_remove_member"])
async def test_remove_member_confirmation_refuses_content_manager(f: Fixtures, action) -> None:
    answer = await dispatch(f, SimpleAction(action, "42"))

    assert answer.calls == [(_OWNER_ONLY_TEXT, True)]
    assert f.membership.removed == []


async def test_persona_template_sets_persona_for_owner(f: Fixtures) -> None:
    """A Пресет button on a pre-#95 /persona message: saved, and that message becomes the
    Экран Настроек."""
    answer = await dispatch(f, SimpleAction("persona_template", "1"), user_id=OWNER_ID)

    assert answer.calls == [(None, None)]
    assert f.owner_settings.values == {"voice": "preset:evidence_analyst"}
    _, message_id, text, _ = f.bot.edited_messages[0]
    assert message_id == 2
    assert text.startswith("✅ Персона изменена: Доказательный аналитик")


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


async def test_second_regenerate_article_press_marks_the_prompt_not_the_card(f: Fixtures) -> None:
    """#80: pressing 🔄 on the Статья card twice regenerates without a comment, but the
    "generating" edit goes to the comment prompt - the card (message 2) keeps its buttons."""
    await dispatch(f, SimpleAction("regenerate_article", "article-1"))
    answer = await dispatch(f, SimpleAction("regenerate_article", "article-1"))

    assert answer.calls == [("Принято, генерирую...", None)]
    assert f.article_regen_op.regenerated == [(ArticleId("article-1"), None)]
    assert f.bot.edited_messages == []
    assert f.article_prompt.generating == [(1, 100)]


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


async def test_approve_redraw_keeps_the_read_button(f: Fixtures) -> None:
    """#92: the redrawn card still opens the Статья's Страница для чтения."""
    f.article.telegraph_path = "Statya-10-07"

    await dispatch(f, SimpleAction("approve", "article-1"))

    _, _, _, keyboard = f.bot.edited_messages[-1]
    urls = [button.url for row in keyboard.inline_keyboard for button in row if button.url]
    assert urls == ["https://telegra.ph/Statya-10-07"]


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

    assert answer.calls == [("Генерирую обложку — пришлю её сюда.", None)]
    # By hand from a Статья card: its result is sent back as a photo (#91).
    assert f.plan.manual_cover_requests == [PlanItemId("item-1")]


async def test_export_article_sends_the_document(f: Fixtures) -> None:
    answer = await dispatch(f, ExportArticle("article-1", "md"))

    assert answer.calls == [(None, None)]
    assert len(f.bot.sent_documents) == 1


async def test_regenerate_prompts_for_a_comment_on_first_press(f: Fixtures) -> None:
    answer = await dispatch(f, SimpleAction("regenerate", "item-1"))

    assert answer.calls == [(None, None)]
    assert f.plan_ops.regenerated == []


async def test_second_regenerate_press_marks_the_prompt_not_the_plan(f: Fixtures) -> None:
    """#80: pressing 🔄 on the Тема twice regenerates without a comment, but the
    "generating" edit goes to the comment prompt - the Plan (message 2) keeps its keyboard."""
    await dispatch(f, SimpleAction("regenerate", "item-1"))
    answer = await dispatch(f, SimpleAction("regenerate", "item-1"))

    assert answer.calls == [("Принято, генерирую...", None)]
    assert f.plan_ops.regenerated == [(PlanItemId("item-1"), None)]
    assert f.bot.edited_messages == []
    assert f.plan_prompt.generating == [(1, 100)]


async def test_cancel_comment_drops_the_plan_wait_and_closes_the_prompt(f: Fixtures) -> None:
    await dispatch(f, SimpleAction("regenerate", "item-1"))

    answer = await dispatch(f, SimpleAction("cancel_comment", "item-1"))

    assert answer.calls == [(None, None)]
    # The flow deletes its prompt; the pressed message is not edited by the dispatcher.
    assert f.plan_prompt.withdrawn == [(1, 100)]
    assert f.bot.edited_messages == []
    assert await f.plan_review.handle_comment_reply(1, CM_ID, "too late", None) is False
    assert f.plan_ops.regenerated == []


async def test_cancel_comment_drops_the_article_wait(f: Fixtures) -> None:
    await dispatch(f, SimpleAction("regenerate_article", "article-1"))

    await dispatch(f, SimpleAction("cancel_comment", "article-1"))

    assert f.article_prompt.withdrawn == [(1, 100)]
    assert f.plan_prompt.withdrawn == []
    assert f.pending_inputs.rows == {}


async def test_cancel_comment_without_a_matching_wait_leaves_the_message(f: Fixtures) -> None:
    """A stale Отмена (or one pressed by another member) cancels nothing, so it must not
    remove a prompt that is still waiting for its owner (#88)."""
    await dispatch(f, SimpleAction("regenerate", "item-1"))

    answer = await dispatch(f, SimpleAction("cancel_comment", "item-1"), user_id=OWNER_ID)

    assert answer.calls == [(None, None)]
    assert f.bot.edited_messages == []
    assert f.plan_prompt.withdrawn == []
    assert len(f.pending_inputs.rows) == 1


async def test_approve_all_approves_and_fans_out_generation(f: Fixtures) -> None:
    answer = await dispatch(f, SimpleAction("approve_all", "plan-1"))

    assert answer.calls == [(None, None)]
    assert f.plan_ops.approved == [PlanItemId("plan-1")]
    assert f.plan.cover_requests == [PlanItemId("item-1")]
    assert len(f.article.requested_generations) > 0


async def test_approve_all_turns_the_pressed_plan_message_into_the_hub(f: Fixtures) -> None:
    """#81/#91: after approving, the pressed Plan message is redrawn from the DB as the Хаб -
    no separate progress message, no review buttons left behind."""
    await dispatch(f, SimpleAction("approve_all", "plan-1"))

    assert f.bot.sent_messages == []
    chat_id, message_id, text, _keyboard = f.bot.edited_messages[-1]
    assert (chat_id, message_id) == (1, 2)
    assert "1. Тема" in text and "🖼 ❌ · Дзен ✅ · VC.ru ❌" in text


async def test_page_on_an_approved_plan_redraws_the_hub(f: Fixtures) -> None:
    f.plan._summary = PlanSummary(id=PlanId("plan-1"), week_label="2026-W33", status="approved")

    await dispatch(f, Page("plan-1", 1))

    assert "🖼 ❌" in f.bot.edited_messages[-1][2]


async def test_hub_topic_opens_the_result_card_in_the_same_message(f: Fixtures) -> None:
    answer = await dispatch(f, SimpleAction("hub_topic", "item-1"))

    assert answer.calls == [(None, None)]
    assert f.plan.hub_views == [(PlanId("plan-1"), PlanItemId("item-1"))]
    chat_id, message_id, text, keyboard = f.bot.edited_messages[-1]
    assert (chat_id, message_id) == (1, 2)
    assert text.startswith("📂 Тема 1 из 1")
    actions = [decode_callback_data(b.callback_data) for r in keyboard.inline_keyboard for b in r]
    assert SimpleAction("hub_article", "article-1") in actions
    assert actions[-1] == SimpleAction("hub_back", "plan-1")


async def test_hub_back_returns_to_the_checklist(f: Fixtures) -> None:
    await dispatch(f, SimpleAction("hub_topic", "item-1"))

    await dispatch(f, SimpleAction("hub_back", "plan-1"))

    assert f.plan.hub_views[-1] == (PlanId("plan-1"), None)
    assert f.bot.edited_messages[-1][2].startswith("📋 План")


async def test_hub_cover_sends_the_photo_on_demand(f: Fixtures) -> None:
    f.plan.cover = PlanItemCoverView(
        plan_item_id=PlanItemId("item-1"), title="Тема", image=b"img", mime_type="image/png"
    )

    answer = await dispatch(f, SimpleAction("hub_cover", "item-1"))

    assert answer.calls == [(None, None)]
    ((chat_id, _photo, caption),) = f.bot.sent_photos
    assert chat_id == 1 and caption == "🖼 Обложка: Тема"


async def test_hub_cover_without_a_cover_just_says_so(f: Fixtures) -> None:
    answer = await dispatch(f, SimpleAction("hub_cover", "item-1"))

    assert answer.calls == [("Обложки пока нет.", None)]
    assert f.bot.sent_photos == []


async def test_hub_article_sends_the_usual_article_card_below(f: Fixtures) -> None:
    f.article.telegraph_path = "Statya-10-07"

    await dispatch(f, SimpleAction("hub_article", "article-1"))

    ((chat_id, text, keyboard),) = f.bot.sent_messages
    assert chat_id == 1 and "Статья" in text
    urls = [b.url for r in keyboard.inline_keyboard for b in r if b.url]
    assert urls == ["https://telegra.ph/Statya-10-07"]


async def test_hub_article_publishes_a_missing_reading_page_first(f: Fixtures) -> None:
    class Publisher:
        async def publish(self, article):
            return "https://telegra.ph/new"

    f.dispatcher._publisher = Publisher()

    await dispatch(f, SimpleAction("hub_article", "article-1"))

    ((_chat_id, _text, keyboard),) = f.bot.sent_messages
    assert [b.url for r in keyboard.inline_keyboard for b in r if b.url] == [
        "https://telegra.ph/new"
    ]


async def test_hub_retry_reruns_every_failed_cell_and_redraws(f: Fixtures) -> None:
    answer = await dispatch(f, SimpleAction("hub_retry", "plan-1"))

    assert answer.calls == [("Повторяю...", None)]
    assert f.plan.cover_requests == [PlanItemId("item-1")]
    assert f.queue.retried == [12]  # only the ❌ Статья, not the ✅ one
    assert len(f.bot.edited_messages) == 1


async def test_hub_retry_topic_reruns_that_topic(f: Fixtures) -> None:
    await dispatch(f, SimpleAction("hub_retry_topic", "item-1"))

    assert f.plan.cover_requests == [PlanItemId("item-1")]
    assert f.queue.retried == [12]


async def test_hub_retry_with_nothing_failed_says_so(f: Fixtures) -> None:
    f.plan.hub = replace(f.plan.hub, topics=[])

    answer = await dispatch(f, SimpleAction("hub_retry", "plan-1"))

    assert answer.calls == [("Повторять нечего.", None)]


async def test_delete_deletes_and_re_renders_the_plan(f: Fixtures) -> None:
    """#81: no separate "Тема удалена." - the pressed Plan message is redrawn from the DB."""
    f.plan._view = PlanView(
        id=PlanId("plan-1"),
        week_label="2026-W33",
        items=[PlanItemView(id=PlanItemId("item-1"), title="Тема", status="rejected")],
    )

    answer = await dispatch(f, SimpleAction("delete", "item-1"))

    assert answer.calls == [(None, None)]
    assert f.plan_ops.deleted == [PlanItemId("item-1")]
    assert f.bot.sent_messages == []
    chat_id, message_id, text, keyboard = f.bot.edited_messages[-1]
    assert (chat_id, message_id) == (1, 2)
    assert "Тема — убрана" in text
    assert keyboard is None


async def test_delete_on_a_later_page_redraws_that_page(f: Fixtures) -> None:
    """Deleting Тема 9 (page 2 of 8-per-page) must not throw the reader back to page 1."""
    items = [
        PlanItemView(id=PlanItemId(f"item-{n}"), title=f"Тема {n}", status="pending_review")
        for n in range(1, 10)
    ]
    f.plan._view = PlanView(id=PlanId("plan-1"), week_label="2026-W33", items=items)

    await dispatch(f, SimpleAction("delete", "item-9"))

    _, _, text, _ = f.bot.edited_messages[-1]
    assert "Страница 2/2" in text
    assert "9. Тема 9" in text


async def test_regenerate_second_press_on_the_plan_message_keeps_the_plan(f: Fixtures) -> None:
    """A second 🔄 on the Plan message (instead of the comment prompt's "Пропустить") enqueues
    without a comment but must not overwrite the Plan with "⏳ Генерирую..." - nothing would
    redraw it if the Job then failed."""
    f.plan.message_ref = PlanMessageRef(chat_id=1, message_id=2)
    await dispatch(f, SimpleAction("regenerate", "item-1"))

    answer = await dispatch(f, SimpleAction("regenerate", "item-1"))

    assert answer.calls == [("Принято, генерирую...", None)]
    assert f.plan_ops.regenerated == [(PlanItemId("item-1"), None)]
    assert all("Генерирую" not in text for _, _, text, _ in f.bot.edited_messages)


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


# --- Главное меню and Экран Настроек (#95) ---

_OWNER_ONLY_MENU_ACTIONS = [
    SimpleAction("menu_members", ""),
    SimpleAction("settings", ""),
    SimpleAction("edit_setting", "niche"),
    SimpleAction("ask_setting", "persona"),
    SimpleAction("pick_setting", "persona:0"),
    SimpleAction("schedule", "m"),
    SimpleAction("schedule_day", "fri:m"),
    SimpleAction("schedule_time", "m"),
    SimpleAction("onboarding_step", "start"),
    SimpleAction("onboarding_step", "niche"),
    SimpleAction("onboarding_pick", "persona:0"),
    SimpleAction("onboarding_later", ""),
    SimpleAction("onboarding_launch", ""),
]


@pytest.mark.parametrize("payload", _OWNER_ONLY_MENU_ACTIONS)
async def test_owner_only_menu_actions_refuse_content_manager(f: Fixtures, payload) -> None:
    answer = await dispatch(f, payload)

    assert answer.calls == [(_OWNER_ONLY_TEXT, True)]
    assert f.bot.edited_messages == [] and f.bot.sent_messages == []
    assert f.owner_settings.values == {} and f.schedule.config is None


@pytest.mark.parametrize(
    "payload",
    [
        SimpleAction("menu", ""),
        SimpleAction("menu_plan", ""),
        SimpleAction("menu_generate_plan", ""),
        SimpleAction("menu_topic", ""),
        SimpleAction("menu_history", ""),
        SimpleAction("cancel_input", "topic_input"),
    ],
)
async def test_shared_menu_actions_refuse_unregistered_caller(f: Fixtures, payload) -> None:
    answer = await dispatch(f, payload, user_id=UNKNOWN_ID)

    assert answer.calls == [(_ACCESS_DENIED_TEXT, True)]
    assert f.bot.edited_messages == [] and f.bot.sent_messages == []


async def test_menu_redraws_the_main_menu_for_the_pressers_role(f: Fixtures) -> None:
    await dispatch(f, SimpleAction("menu", ""))
    await dispatch(f, SimpleAction("menu", ""), user_id=OWNER_ID)

    cm_menu, owner_menu = (edit[3] for edit in f.bot.edited_messages)
    assert len(cm_menu.inline_keyboard) == 3
    assert len(owner_menu.inline_keyboard) == 6


async def test_menu_plan_shows_the_plan_screen_in_place(f: Fixtures) -> None:
    await dispatch(f, SimpleAction("menu_plan", ""))

    ((_, message_id, text, _),) = f.bot.edited_messages
    assert message_id == 2 and "пока нет" in text


async def test_menu_generate_plan_requests_the_weeks_plan(f: Fixtures) -> None:
    await dispatch(f, SimpleAction("menu_generate_plan", ""))

    assert len(f.plan.requested_weeks) == 1


async def test_menu_topic_asks_for_a_topic(f: Fixtures) -> None:
    await dispatch(f, SimpleAction("menu_topic", ""))

    assert f.pending_inputs.rows[(1, CM_ID)].kind == "topic_input"


async def test_menu_history_opens_the_history_in_place_of_the_menu(f: Fixtures) -> None:
    await dispatch(f, SimpleAction("menu_history", ""))

    assert f.bot.sent_messages == []
    chat_id, message_id, text, keyboard = f.bot.edited_messages[-1]
    assert (chat_id, message_id) == (1, 2)
    assert text.startswith("🗂 История")
    assert keyboard.inline_keyboard[-1][0].callback_data == "mn:"


async def test_menu_members_opens_the_member_list_in_place_of_the_menu(f: Fixtures) -> None:
    await dispatch(f, SimpleAction("menu_members", ""), user_id=OWNER_ID)

    assert f.bot.sent_messages == []
    chat_id, message_id, text, keyboard = f.bot.edited_messages[-1]
    assert (chat_id, message_id) == (1, 2)
    assert text.startswith("👥 Участники")
    assert keyboard.inline_keyboard[-1][0].callback_data == "mn:"


async def test_settings_and_edit_setting_work_for_owner(f: Fixtures) -> None:
    await dispatch(f, SimpleAction("settings", ""), user_id=OWNER_ID)
    await dispatch(f, SimpleAction("edit_setting", "niche"), user_id=OWNER_ID)

    assert f.bot.edited_messages[0][2].startswith("⚙️ Настройки")
    pending = f.pending_inputs.rows[(1, OWNER_ID)]
    assert (pending.kind, pending.target_id) == (SETTING_INPUT_KIND, "niche:2")


async def test_pick_setting_saves_the_preset(f: Fixtures) -> None:
    await dispatch(f, SimpleAction("pick_setting", "persona:2"), user_id=OWNER_ID)

    assert f.owner_settings.values == {"voice": "preset:founder_operator"}


async def test_schedule_day_reschedules(f: Fixtures) -> None:
    await dispatch(f, SimpleAction("schedule_day", "fri:s"), user_id=OWNER_ID)

    assert f.schedule.config == ScheduleConfig("fri", 9, 0)
    assert f.scheduler.rescheduled == ["weekly_plan_trigger"]


async def test_schedule_time_asks_for_the_time(f: Fixtures) -> None:
    await dispatch(f, SimpleAction("schedule_time", "m"), user_id=OWNER_ID)

    assert f.pending_inputs.rows[(1, OWNER_ID)].target_id == "time:2:m"


async def test_cancel_input_drops_the_pressers_wait_and_its_prompt(f: Fixtures) -> None:
    await dispatch(f, SimpleAction("menu_topic", ""))

    answer = await dispatch(f, SimpleAction("cancel_input", "topic_input"))

    assert answer.calls == [(None, None)]
    assert f.pending_inputs.rows == {}
    assert f.bot.deleted_messages == [(1, 1), (1, 2)]


async def test_cancel_under_someone_elses_question_keeps_the_pressers_own_wait(
    f: Fixtures,
) -> None:
    """In a group two members can each have a question open; «Отмена» under the Owner's
    question pressed by the Контент-менеджер must not drop the Контент-менеджер's own."""
    await dispatch(f, SimpleAction("menu_topic", ""), user_id=OWNER_ID)  # messages 1, 2
    await dispatch(f, SimpleAction("menu_topic", ""))  # messages 3, 4
    answer = FakeAnswerer()
    callback_input = CallbackInput(
        chat_id=1,
        message_id=1,
        user_id=CM_ID,
        username="cm",
        payload=SimpleAction("cancel_input", "topic_input"),
    )

    await f.dispatcher.dispatch(callback_input, answer)

    assert (1, CM_ID) in f.pending_inputs.rows
    assert (1, OWNER_ID) in f.pending_inputs.rows
    assert f.bot.deleted_messages == []


async def test_stale_cancel_in_a_group_leaves_the_message(f: Fixtures) -> None:
    await dispatch(f, SimpleAction("cancel_input", "topic_input"))

    assert f.bot.deleted_messages == []


async def test_stale_cancel_in_a_private_chat_removes_the_prompt(f: Fixtures) -> None:
    answer = FakeAnswerer()
    callback_input = CallbackInput(
        chat_id=CM_ID,
        message_id=9,
        user_id=CM_ID,
        username="cm",
        payload=SimpleAction("cancel_input", "topic_input"),
    )

    await f.dispatcher.dispatch(callback_input, answer)

    assert f.bot.deleted_messages == [(CM_ID, 9)]


# --- onboarding (#96) ---


async def test_onboarding_start_asks_the_first_step_for_the_owner(f: Fixtures) -> None:
    await dispatch(f, SimpleAction("onboarding_step", "start"), user_id=OWNER_ID)

    assert f.owner_settings.values["onboarding"] == "started"
    assert f.pending_inputs.rows[(1, OWNER_ID)].kind == ONBOARDING_INPUT_KIND
    assert f.bot.deleted_messages == [(1, 2)]  # the intro the button was on


async def test_onboarding_pick_saves_the_preset(f: Fixtures) -> None:
    await dispatch(f, SimpleAction("onboarding_pick", "persona:0"), user_id=OWNER_ID)

    assert "voice" in f.owner_settings.values


async def test_onboarding_launch_starts_the_first_plan_once(f: Fixtures) -> None:
    await dispatch(f, SimpleAction("onboarding_launch", ""), user_id=OWNER_ID)
    await dispatch(f, SimpleAction("onboarding_launch", ""), user_id=OWNER_ID)

    assert len(f.plan.requested_weeks) == 1
    assert f.owner_settings.values["onboarding"] == "done"


async def test_onboarding_later_ends_it_and_shows_the_menu(f: Fixtures) -> None:
    await dispatch(f, SimpleAction("onboarding_later", ""), user_id=OWNER_ID)

    assert f.owner_settings.values["onboarding"] == "later"
    ((_, message_id, text, _),) = f.bot.edited_messages
    assert message_id == 2 and text.startswith("🏠 Главное меню")


async def test_cancelling_a_wizard_question_says_how_to_come_back(f: Fixtures) -> None:
    await dispatch(f, SimpleAction("onboarding_step", "niche"), user_id=OWNER_ID)
    question = f.pending_inputs.rows[(1, OWNER_ID)].prompt_message_id
    answer = FakeAnswerer()

    await f.dispatcher.dispatch(
        CallbackInput(
            chat_id=1,
            message_id=question,
            user_id=OWNER_ID,
            username=None,
            payload=SimpleAction("cancel_input", ONBOARDING_INPUT_KIND),
        ),
        answer,
    )

    assert (1, OWNER_ID) not in f.pending_inputs.rows
    assert "/start" in f.bot.sent_messages[-1][1]
