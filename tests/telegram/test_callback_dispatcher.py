"""Unit tests for `CallbackDispatcher` (candidate 04, ADR-0012, ADR-0015) - built and driven
entirely through hand-built `CallbackInput` values and fakes, no aiogram `CallbackQuery`
involved. The cross-tenant table over every Action with an id is in
`test_callback_cross_tenant.py`, on a real database."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest
from aiogram.types import BufferedInputFile, InlineKeyboardMarkup

from content_zavod.domain import HubArticleCell, HubTopic, PlanHubView, PlanItemCoverView
from content_zavod.domain.plan import PlanItemDetail
from content_zavod.job_queue.models import JobSnapshot
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
from content_zavod.telegram.asset_photos import AssetPhotos
from content_zavod.telegram.callback_dispatcher import CallbackDispatcher, CallbackInput
from content_zavod.telegram.gateway import SentPhoto
from content_zavod.telegram.guide import Guide
from content_zavod.telegram.input_prompt import InputPrompt
from content_zavod.telegram.main_menu import MainMenu
from content_zavod.telegram.onboarding import ONBOARDING_INPUT_KIND, Onboarding
from content_zavod.telegram.pending_inputs import PendingInput
from content_zavod.telegram.settings_screen import SETTING_INPUT_KIND, SettingsScreen
from content_zavod.telegram.style_samples import STYLE_SAMPLES_KIND, StyleSamples
from content_zavod.workspaces import Workspace
from tests.tenancy import WS_A

from .fakes import FakeJobs, FakePendingInputs

USER = 1  # a private chat's id is its user's id
WORKSPACE = Workspace(WS_A, USER, None, "Europe/Moscow", None, None)


class FakeBot:
    """Satisfies `BotClient` so tests can wrap it in the real `TelegramGateway`."""

    def __init__(self) -> None:
        self.sent_messages: list[tuple[int, str, InlineKeyboardMarkup | None]] = []
        self.sent_documents: list[tuple[int, BufferedInputFile, str | None]] = []
        self.edited_messages: list[tuple[int, int, str, InlineKeyboardMarkup | None]] = []
        self.deleted_messages: list[tuple[int, int]] = []
        self.sent_photos: list[tuple[int, BufferedInputFile | str, str | None]] = []
        self.edited_media: list[tuple[int, int, BufferedInputFile | str, str | None]] = []
        self.pinned: list[tuple[int, int]] = []
        self.fail_pin = False

    async def send_message(self, chat_id, text, reply_markup=None, parse_mode=None) -> int:
        self.sent_messages.append((chat_id, text, reply_markup))
        return len(self.sent_messages)

    async def send_document(self, chat_id, document, caption=None) -> None:
        self.sent_documents.append((chat_id, document, caption))

    async def send_photo(self, chat_id, photo, caption=None, reply_markup=None) -> SentPhoto:
        self.sent_photos.append((chat_id, photo, caption))
        return SentPhoto(1000 + len(self.sent_photos), f"file-{len(self.sent_photos)}")

    async def edit_message_media(
        self, chat_id, message_id, photo, caption=None, reply_markup=None
    ) -> str | None:
        self.edited_media.append((chat_id, message_id, photo, caption))
        return None

    async def pin_chat_message(self, chat_id, message_id) -> None:
        if self.fail_pin:
            raise RuntimeError("not enough rights to pin a message")
        self.pinned.append((chat_id, message_id))

    async def edit_message_text(self, chat_id, message_id, text, reply_markup=None) -> None:
        self.edited_messages.append((chat_id, message_id, text, reply_markup))

    async def edit_message_reply_markup(self, chat_id, message_id, reply_markup=None) -> None:
        self.edited_messages.append((chat_id, message_id, "", reply_markup))

    async def delete_message(self, chat_id, message_id) -> None:
        self.deleted_messages.append((chat_id, message_id))

    async def set_my_commands(self, commands, *, scope) -> None:
        pass


class FakePlan:
    def __init__(self) -> None:
        self.covers_enabled = True
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

    async def get(self, workspace_id, plan_id: PlanId) -> PlanView:
        return self._view

    async def find_active(self, workspace_id, week_label: str) -> PlanView | None:
        return None

    async def request_new(
        self, workspace_id, week_label: str, *, generation_id: str | None = None
    ) -> int:
        self.requested_weeks.append(week_label)
        return 1

    async def get_plan_id_for_item(self, workspace_id, plan_item_id: PlanItemId) -> PlanId:
        return self._view.id

    async def get_message_ref(self, workspace_id, plan_id: PlanId) -> PlanMessageRef | None:
        return self.message_ref

    async def get_summary(self, workspace_id, plan_id: PlanId) -> PlanSummary:
        return self._summary

    async def list_page(
        self, workspace_id, *, page: int, page_size: int
    ) -> tuple[list[PlanSummary], int]:
        return [self._summary], 1

    async def request_cover(
        self, workspace_id, plan_item_id: PlanItemId, *, manual: bool = False
    ) -> None:
        (self.manual_cover_requests if manual else self.cover_requests).append(plan_item_id)

    async def get_hub(self, workspace_id, plan_id: PlanId) -> PlanHubView:
        open_item = self.hub_views[-1][1] if self.hub_views else None
        return replace(self.hub, open_item_id=open_item)

    async def set_hub_view(
        self, workspace_id, plan_id: PlanId, plan_item_id: PlanItemId | None
    ) -> None:
        self.hub_views.append((plan_id, plan_item_id))

    async def get_cover(self, workspace_id, plan_item_id: PlanItemId) -> PlanItemCoverView | None:
        return self.cover

    async def approved_items(self, workspace_id, plan_id: PlanId) -> list[PlanItemDetail]:
        return self._approved_items

    async def request_replacement(self, workspace_id, plan_id: PlanId) -> None:
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

    async def get(self, workspace_id, article_id: ArticleId) -> ArticleView:
        return self._view

    async def mark_exported(self, workspace_id, article_id: ArticleId) -> None:
        self.mark_exported_calls.append(article_id)

    async def get_telegraph_path(self, workspace_id, article_id: ArticleId) -> str | None:
        return self.telegraph_path

    async def request_generation(
        self, workspace_id, plan_id, plan_item_id, title, summary, keywords, platform
    ) -> str:
        self.requested_generations.append(
            (plan_id, plan_item_id, title, summary, keywords, platform)
        )
        return "job-1"

    async def list_summary_for_plan(self, workspace_id, plan_id: PlanId) -> list[ArticleSummary]:
        return [self._summary]

    async def get_summary(self, workspace_id, article_id: ArticleId) -> ArticleSummary:
        return self._summary

    async def get_plan_id(self, workspace_id, article_id: ArticleId) -> PlanId:
        return PlanId("plan-1")

    async def list_versions(
        self, workspace_id, article_id: ArticleId
    ) -> list[ArticleVersionSummary]:
        return [
            ArticleVersionSummary(id=1, model="m", tokens=1, cost=0.1, created_at=datetime.now(UTC))
        ]

    async def get_version(
        self, workspace_id, article_id: ArticleId, version_id: int
    ) -> ArticleVersionView:
        return self._version


class FakePlanOps:
    def __init__(self) -> None:
        self.deleted: list[PlanItemId] = []
        self.regenerated: list[tuple[PlanItemId, str | None]] = []
        self.approved: list[PlanItemId] = []

    async def delete_item(self, workspace_id, plan_item_id: PlanItemId) -> None:
        self.deleted.append(plan_item_id)

    async def regenerate_item(
        self, workspace_id, plan_item_id: PlanItemId, comment: str | None
    ) -> None:
        self.regenerated.append((plan_item_id, comment))

    async def approve_all(self, workspace_id, plan_item_id: PlanItemId) -> None:
        self.approved.append(plan_item_id)


class FakePrompt:
    """Each prompt is one message, id 100."""

    def __init__(self) -> None:
        self.prompted: list[tuple[int, object]] = []
        self.generating: list[tuple[int, int]] = []
        self.withdrawn: list[tuple[int, int]] = []

    async def prompt_for_comment(self, chat_id: int, id_: object) -> int:
        self.prompted.append((chat_id, id_))
        return 100

    async def mark_generating(self, chat_id: int, pending: PendingInput) -> None:
        self.generating.append((chat_id, pending.prompt_message_id))

    async def withdraw(self, chat_id: int, pending: PendingInput) -> None:
        self.withdrawn.append((chat_id, pending.prompt_message_id))


class FakeArticleRegen:
    def __init__(self) -> None:
        self.regenerated: list[tuple[ArticleId, str | None]] = []

    async def __call__(self, workspace_id, article_id: ArticleId, comment: str | None) -> None:
        self.regenerated.append((article_id, comment))


class FakeOwnerSettingsStore:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    async def get(self, workspace_id, key: str) -> str | None:
        return self.values.get(key)

    async def set(self, workspace_id, key: str, value: str) -> None:
        self.values[key] = value

    async def set_if_changed(self, workspace_id, key: str, value: str) -> bool:
        changed = self.values.get(key) != value
        self.values[key] = value
        return changed


class FakeSchedule:
    def __init__(self) -> None:
        self.config: ScheduleConfig | None = None

    async def get(self, workspace_id) -> ScheduleConfig | None:
        return self.config

    async def set(self, workspace_id, day_of_week: str, hour: int, minute: int) -> None:
        self.config = ScheduleConfig(day_of_week, hour, minute)


class FakeWorkspaces:
    def __init__(self) -> None:
        self.timezone = "Europe/Moscow"

    async def set_timezone(self, workspace_id, timezone: str) -> None:
        self.timezone = timezone


class FakeBotState:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def set(self, key: str, value: str) -> None:
        self.values[key] = value


class FakeQueue:
    def __init__(self) -> None:
        self.retried: list[int] = []
        self.job_types: dict[int, str] = {}

    async def get_job(self, workspace_id, job_id: int) -> JobSnapshot | None:
        job_type = self.job_types.get(job_id)
        return None if job_type is None else JobSnapshot(job_id, job_type, "failed", {})

    async def get_status(self, workspace_id, job_id: int) -> str:
        return "queued"

    async def retry(self, workspace_id, job_id: int) -> bool:
        self.retried.append(job_id)
        return True


class FakeAnswerer:
    def __init__(self) -> None:
        self.calls: list[tuple[str | None, bool | None]] = []

    async def __call__(self, text: str | None = None, show_alert: bool | None = None) -> None:
        self.calls.append((text, show_alert))


async def _no_download(file_id: str) -> bytes:
    raise AssertionError("no file here")


class Fixtures:
    def __init__(self, *, plan: FakePlan | None = None) -> None:
        self.bot = FakeBot()
        self.gateway = TelegramGateway(self.bot)
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
        self.owner_settings = FakeOwnerSettingsStore()
        self.schedule = FakeSchedule()
        self.workspace = FakeWorkspaces()
        self.queue = FakeQueue()
        self.prompts = InputPrompt(self.bot, self.pending_inputs)
        self.settings_screen = SettingsScreen(
            SettingsService(self.owner_settings),
            self.schedule,
            self.workspace,
            self.bot,
            self.prompts,
        )
        self.main_menu = MainMenu(
            self.plan,
            self.queue,
            self.schedule,
            self.bot,
            self.gateway,
            self.prompts,
            settings=SettingsService(self.owner_settings),
            onboarding=OnboardingState(self.owner_settings),
        )
        self.onboarding = Onboarding(
            OnboardingState(self.owner_settings),
            SettingsService(self.owner_settings),
            self.prompts,
            self.bot,
            self.main_menu,
            self.schedule,
        )
        self.photos = AssetPhotos(self.bot, FakeBotState())
        self.guide = Guide(self.photos, self.bot)
        self.style_samples = StyleSamples(
            FakeJobs(),
            SettingsService(self.owner_settings),
            self.pending_inputs,
            self.prompts,
            self.bot,
            _no_download,
            OnboardingState(self.owner_settings),
        )
        self.dispatcher = CallbackDispatcher(
            self.plan,
            self.article,
            self.gateway,
            self.bot,
            self.plan_review,
            self.article_regeneration,
            self.settings_screen,
            self.queue,
            self.main_menu,
            self.prompts,
            self.onboarding,
            guide=self.guide,
            style_samples=self.style_samples,
        )


@pytest.fixture
def f() -> Fixtures:
    return Fixtures()


def make_input(payload, *, message_id: int = 2) -> CallbackInput:
    return CallbackInput(
        chat_id=USER, message_id=message_id, user_id=USER, username="owner", payload=payload
    )


async def dispatch(f: Fixtures, payload, *, message_id: int = 2) -> FakeAnswerer:
    answer = FakeAnswerer()
    await f.dispatcher.dispatch(WORKSPACE, make_input(payload, message_id=message_id), answer)
    return answer


async def test_persona_template_sets_persona_for_owner(f: Fixtures) -> None:
    """A Пресет button on a pre-#95 /persona message: saved, and that message becomes the
    Экран Настроек."""
    answer = await dispatch(f, SimpleAction("persona_template", "1"))

    assert answer.calls == [(None, None)]
    assert f.owner_settings.values == {"voice": "preset:evidence_analyst"}
    _, message_id, text, _ = f.bot.edited_messages[0]
    assert message_id == 2
    assert text.startswith("✅ Стиль автора изменён: Доказательный аналитик")


# --- Действия on the Plan, Статьи and История ---


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
    answer = await dispatch(f, SimpleAction("cancel_regenerate_plan", "plan-1"))

    assert answer.calls == [(None, None)]
    assert f.bot.edited_messages[-1][2] == "Отменено."


async def test_retry_requeues_the_job(f: Fixtures) -> None:
    f.queue.job_types[7] = "generate_article"

    answer = await dispatch(f, SimpleAction("retry", "7"))

    assert answer.calls == [(None, None)]
    assert f.queue.retried == [7]


async def test_retry_of_a_cover_with_covers_off_says_so_and_requeues_nothing(
    f: Fixtures,
) -> None:
    """#123: «🔁 Повторить» under a cover failure sent before `YANDEX_COVERS=off`."""
    f.plan.covers_enabled = False
    f.queue.job_types[7] = "generate_cover"

    answer = await dispatch(f, SimpleAction("retry", "7"))

    assert answer.calls == [("Обложки сейчас выключены.", None)]
    assert f.queue.retried == []


async def test_retry_of_an_article_with_covers_off_still_requeues(f: Fixtures) -> None:
    f.plan.covers_enabled = False
    f.queue.job_types[7] = "regenerate_article"

    await dispatch(f, SimpleAction("retry", "7"))

    assert f.queue.retried == [7]


async def test_retry_of_a_job_this_workspace_does_not_have_is_stale(f: Fixtures) -> None:
    """ADR-0015: «🔁 Повторить» naming another Пространство's Задача (or one from before the
    migration) - same answer as a stale button, nothing requeued."""
    answer = await dispatch(f, SimpleAction("retry", "7"))

    assert answer.calls == [("Кнопка устарела.", True)]
    assert f.queue.retried == []


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

    async def not_ready(workspace_id, article_id: ArticleId) -> None:
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


async def test_request_cover_with_covers_off_says_so_and_requests_nothing(f: Fixtures) -> None:
    """#123: «🖼 Обложка» on a card sent before `YANDEX_COVERS=off`."""
    f.plan.covers_enabled = False

    answer = await dispatch(f, SimpleAction("request_cover", "item-1"))

    assert answer.calls == [("Обложки сейчас выключены.", None)]
    assert f.plan.manual_cover_requests == []


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
    assert await f.plan_review.handle_comment_reply(WS_A, USER, USER, "too late") is False
    assert f.plan_ops.regenerated == []


async def test_cancel_comment_drops_the_article_wait(f: Fixtures) -> None:
    await dispatch(f, SimpleAction("regenerate_article", "article-1"))

    await dispatch(f, SimpleAction("cancel_comment", "article-1"))

    assert f.article_prompt.withdrawn == [(1, 100)]
    assert f.plan_prompt.withdrawn == []
    assert f.pending_inputs.rows == {}


async def test_cancel_comment_without_a_matching_wait_leaves_the_message(f: Fixtures) -> None:
    """A stale Отмена (for an earlier Тема) cancels nothing, so it must not remove a prompt
    that is still waiting (#88)."""
    await dispatch(f, SimpleAction("regenerate", "item-1"))

    answer = await dispatch(f, SimpleAction("cancel_comment", "item-2"))

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


async def test_hub_button_redraws_the_pressed_message_even_if_the_ref_points_elsewhere(
    f: Fixtures,
) -> None:
    """#106: a Хаб button lives on a message that exists (it was just pressed), so it redraws
    that one - never the stored ref, which may be a deleted message or a replacement."""
    f.plan.message_ref = PlanMessageRef(chat_id=1, message_id=99)

    await dispatch(f, SimpleAction("hub_back", "plan-1"))

    assert [(c, m) for c, m, _, _ in f.bot.edited_messages] == [(1, 2)]
    assert f.bot.sent_messages == []


async def test_hub_cover_sends_the_photo_on_demand(f: Fixtures) -> None:
    f.plan.cover = PlanItemCoverView(
        plan_item_id=PlanItemId("item-1"), title="Тема", image=b"img", mime_type="image/png"
    )

    answer = await dispatch(f, SimpleAction("hub_cover", "item-1"))

    assert answer.calls == [(None, None)]
    ((chat_id, _photo, caption),) = f.bot.sent_photos
    assert chat_id == USER and caption == "🖼 Обложка: Тема"


async def test_hub_cover_without_a_cover_just_says_so(f: Fixtures) -> None:
    answer = await dispatch(f, SimpleAction("hub_cover", "item-1"))

    assert answer.calls == [("Обложки пока нет.", None)]
    assert f.bot.sent_photos == []


async def test_hub_cover_with_covers_off_says_covers_are_off(f: Fixtures) -> None:
    """#123: a stale Хаб «🖼 Обложка» must not promise a cover that will never come."""
    f.plan.covers_enabled = False

    answer = await dispatch(f, SimpleAction("hub_cover", "item-1"))

    assert answer.calls == [("Обложки сейчас выключены.", None)]


async def test_hub_article_sends_the_usual_article_card_below(f: Fixtures) -> None:
    f.article.telegraph_path = "Statya-10-07"

    await dispatch(f, SimpleAction("hub_article", "article-1"))

    ((chat_id, text, keyboard),) = f.bot.sent_messages
    assert chat_id == USER and "Статья" in text
    urls = [b.url for r in keyboard.inline_keyboard for b in r if b.url]
    assert urls == ["https://telegra.ph/Statya-10-07"]


async def test_hub_article_publishes_a_missing_reading_page_first(f: Fixtures) -> None:
    class Publisher:
        async def publish(self, workspace_id, article):
            assert workspace_id == WS_A
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


# --- a DomainError raised mid-branch is answered as a show_alert, not raised ---


async def test_domain_error_from_a_branch_is_answered_not_raised(f: Fixtures) -> None:
    from content_zavod.domain import DomainError

    async def boom(workspace_id, plan_item_id: PlanItemId) -> None:
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

    async def boom(workspace_id, plan_item_id: PlanItemId) -> None:
        raise PlanItemNotEditable(plan_item_id, "approved")

    f.plan_ops.delete_item = boom  # type: ignore[method-assign]

    answer = await dispatch(f, SimpleAction("delete", "item-1"))

    assert answer.calls[-1] == ("Эту Тему уже нельзя изменить.", True)


# --- Главное меню and Экран Настроек (#95) ---


async def test_menu_redraws_the_main_menu_in_place(f: Fixtures) -> None:
    await dispatch(f, SimpleAction("menu", ""))

    ((_, message_id, _, menu),) = f.bot.edited_messages
    assert message_id == 2
    assert len(menu.inline_keyboard) == 6


async def test_menu_plan_shows_the_plan_screen_in_place(f: Fixtures) -> None:
    f.owner_settings.values["niche"] = "кофейни"

    await dispatch(f, SimpleAction("menu_plan", ""))

    ((_, message_id, text, _),) = f.bot.edited_messages
    assert message_id == 2 and "пока нет" in text


async def test_menu_generate_plan_requests_the_weeks_plan(f: Fixtures) -> None:
    f.owner_settings.values["niche"] = "кофейни"

    await dispatch(f, SimpleAction("menu_generate_plan", ""))

    assert len(f.plan.requested_weeks) == 1


async def test_menu_topic_asks_for_a_topic(f: Fixtures) -> None:
    await dispatch(f, SimpleAction("menu_topic", ""))

    assert f.pending_inputs.rows[(USER, USER)].kind == "topic_input"


async def test_menu_history_opens_the_history_in_place_of_the_menu(f: Fixtures) -> None:
    await dispatch(f, SimpleAction("menu_history", ""))

    assert f.bot.sent_messages == []
    chat_id, message_id, text, keyboard = f.bot.edited_messages[-1]
    assert (chat_id, message_id) == (1, 2)
    assert text.startswith("🗂 История")
    assert keyboard.inline_keyboard[-1][0].callback_data == "mn:"


async def test_settings_and_edit_setting_work_for_owner(f: Fixtures) -> None:
    await dispatch(f, SimpleAction("settings", ""))
    await dispatch(f, SimpleAction("edit_setting", "niche"))

    assert f.bot.edited_messages[0][2].startswith("⚙️ Настройки")
    pending = f.pending_inputs.rows[(USER, USER)]
    assert (pending.kind, pending.target_id) == (SETTING_INPUT_KIND, "niche:2")


async def test_pick_setting_saves_the_preset(f: Fixtures) -> None:
    await dispatch(f, SimpleAction("pick_setting", "persona:2"))

    assert f.owner_settings.values == {"voice": "preset:founder_operator"}


async def test_schedule_day_saves_the_day(f: Fixtures) -> None:
    await dispatch(f, SimpleAction("schedule_day", "fri:s"))

    assert f.schedule.config == ScheduleConfig("fri", 9, 0)


async def test_schedule_tz_opens_the_chooser_and_a_pick_saves_it(f: Fixtures) -> None:
    await dispatch(f, SimpleAction("schedule_tz", "s"))
    await dispatch(f, SimpleAction("schedule_tz_pick", "0:s"))

    assert f.bot.edited_messages[0][2].startswith("🌍 Выберите часовой пояс")
    assert f.workspace.timezone == "Europe/Kaliningrad"
    assert f.bot.edited_messages[1][2].startswith("✅ Часовой пояс изменён: МСК−1")


async def test_schedule_time_asks_for_the_time(f: Fixtures) -> None:
    await dispatch(f, SimpleAction("schedule_time", "m"))

    assert f.pending_inputs.rows[(USER, USER)].target_id == "time:2:m"


async def test_cancel_input_drops_the_pressers_wait_and_its_prompt(f: Fixtures) -> None:
    await dispatch(f, SimpleAction("menu_topic", ""))  # the question is message 1

    answer = await dispatch(f, SimpleAction("cancel_input", "topic_input"), message_id=1)

    assert answer.calls == [(None, None)]
    assert f.pending_inputs.rows == {}
    assert f.bot.deleted_messages == [(USER, 1)]


async def test_stale_cancel_removes_the_prompt(f: Fixtures) -> None:
    await dispatch(f, SimpleAction("cancel_input", "topic_input"), message_id=9)

    assert f.bot.deleted_messages == [(USER, 9)]


# --- onboarding (#96) ---


async def test_onboarding_start_asks_the_first_step_for_the_owner(f: Fixtures) -> None:
    await dispatch(f, SimpleAction("onboarding_step", "start"))

    assert f.owner_settings.values["onboarding"] == "started"
    assert f.pending_inputs.rows[(USER, USER)].kind == ONBOARDING_INPUT_KIND
    assert f.bot.deleted_messages == [(1, 2)]  # the intro the button was on


async def test_onboarding_pick_saves_the_preset(f: Fixtures) -> None:
    await dispatch(f, SimpleAction("onboarding_pick", "persona:0"))

    assert "voice" in f.owner_settings.values


async def test_onboarding_launch_starts_the_first_plan_once(f: Fixtures) -> None:
    f.owner_settings.values["niche"] = "кофейни"
    await dispatch(f, SimpleAction("onboarding_launch", ""))
    await dispatch(f, SimpleAction("onboarding_launch", ""))

    assert len(f.plan.requested_weeks) == 1
    assert f.owner_settings.values["onboarding"] == "done"


async def test_onboarding_later_ends_it_and_shows_the_menu(f: Fixtures) -> None:
    await dispatch(f, SimpleAction("onboarding_later", ""))

    assert f.owner_settings.values["onboarding"] == "later"
    ((_, message_id, text, _),) = f.bot.edited_messages
    assert message_id == 2 and text.startswith("🏠 Главное меню")


async def test_cancelling_a_wizard_question_says_how_to_come_back(f: Fixtures) -> None:
    await dispatch(f, SimpleAction("onboarding_step", "niche"))
    question = f.pending_inputs.rows[(USER, USER)].prompt_message_id

    await dispatch(f, SimpleAction("cancel_input", ONBOARDING_INPUT_KIND), message_id=question)

    assert (USER, USER) not in f.pending_inputs.rows
    assert "/start" in f.bot.sent_messages[-1][1]


# --- the Инструкция (#114) ---


async def test_guide_from_the_menu_replaces_it_with_the_carousel(f: Fixtures) -> None:
    answer = await dispatch(f, SimpleAction("guide", "m"))

    assert answer.calls == [(None, None)]
    assert f.bot.deleted_messages == [(1, 2)]
    ((chat_id, photo, caption),) = f.bot.sent_photos
    assert chat_id == USER and photo.filename == "about.png"
    assert caption.startswith("🤖 Что делает бот")


async def test_guide_from_another_message_leaves_it_in_place(f: Fixtures) -> None:
    await dispatch(f, SimpleAction("guide", ""))

    assert f.bot.deleted_messages == []
    assert len(f.bot.sent_photos) == 1


async def test_guide_slide_turns_the_carousel(f: Fixtures) -> None:
    answer = await dispatch(f, SimpleAction("guide_slide", "3"))

    assert answer.calls == [(None, None)]
    ((_, _, photo, caption),) = f.bot.edited_media
    assert photo.filename == "niche.png" and caption.startswith("🎯 Ниша")


async def test_to_menu_from_the_carousel_replaces_it_with_the_menu(f: Fixtures) -> None:
    await dispatch(f, SimpleAction("menu", "g"))

    assert f.bot.deleted_messages == [(1, 2)]
    ((chat_id, text, _),) = f.bot.sent_messages
    assert chat_id == USER and text.startswith("🏠 Главное меню")


# --- Стиль автора по образцам (#134) ---


async def test_style_start_asks_for_samples_and_cancel_drops_the_wait(f: Fixtures) -> None:
    await dispatch(f, SimpleAction("style_start", "a"))

    question_id = len(f.bot.sent_messages)
    assert "Пришлите 1–3 свои статьи" in f.bot.sent_messages[-1][1]
    assert f.pending_inputs.rows[(USER, USER)].kind == STYLE_SAMPLES_KIND

    await dispatch(f, SimpleAction("cancel_input", STYLE_SAMPLES_KIND), message_id=question_id)

    assert (USER, USER) not in f.pending_inputs.rows
    assert (USER, question_id) in f.bot.deleted_messages


async def test_style_presets_turns_the_message_into_the_chooser(f: Fixtures) -> None:
    await dispatch(f, SimpleAction("style_start", "a"))

    await dispatch(f, SimpleAction("style_presets", ""), message_id=7)

    assert (USER, USER) not in f.pending_inputs.rows
    _, message_id, text, _ = f.bot.edited_messages[-1]
    assert message_id == 7 and text.startswith("Стиль автора — ")


async def test_style_done_with_nothing_sent_is_an_alert(f: Fixtures) -> None:
    await dispatch(f, SimpleAction("style_start", "a"))

    answer = await dispatch(f, SimpleAction("style_done", "a"), message_id=len(f.bot.sent_messages))

    assert answer.calls == [("Сначала пришлите текст, файл или ссылку.", True)]


async def test_style_buttons_of_a_build_without_the_flow_are_stale(f: Fixtures) -> None:
    f.dispatcher._style_samples = None

    answer = await dispatch(f, SimpleAction("style_ok", "1"))

    assert answer.calls == [("Кнопка устарела.", True)]


async def _style_question_from_the_wizard(f: Fixtures) -> int:
    """The wizard's Стиль автора step, then «📄 Пришлю статью» on it."""
    await dispatch(f, SimpleAction("onboarding_step", "persona"))
    await dispatch(f, SimpleAction("style_start", "a:o"), message_id=len(f.bot.sent_messages))
    return len(f.bot.sent_messages)


async def test_cancel_of_samples_asked_from_the_wizard_returns_to_its_step(f: Fixtures) -> None:
    question = await _style_question_from_the_wizard(f)

    await dispatch(f, SimpleAction("cancel_input", STYLE_SAMPLES_KIND), message_id=question)

    assert (USER, question) in f.bot.deleted_messages
    assert f.bot.sent_messages[-1][1].startswith("Шаг 3 из 4 · Стиль автора")
    assert f.pending_inputs.rows[(USER, USER)].kind == ONBOARDING_INPUT_KIND


async def test_presets_from_the_wizard_return_to_its_step_not_to_settings(f: Fixtures) -> None:
    question = await _style_question_from_the_wizard(f)

    await dispatch(f, SimpleAction("style_presets", "o"), message_id=question)

    assert f.bot.sent_messages[-1][1].startswith("Шаг 3 из 4 · Стиль автора")
    assert f.pending_inputs.rows[(USER, USER)].kind == ONBOARDING_INPUT_KIND
    assert not any(text.startswith("Стиль автора — ") for _, _, text, _ in f.bot.edited_messages)
