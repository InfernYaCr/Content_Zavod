"""Cross-tenant callbacks (ADR-0015, #118): every Действие that carries an id, pressed by A with
an id of Workspace B, answers «Кнопка устарела» - the same as a stale button - sends or edits
nothing, and leaves B's rows exactly as they were. A real database, real repositories."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import get_args

import asyncpg
import pytest
import pytest_asyncio

from content_zavod.domain import Article, GeneratedVersion, Plan, TopicDraft
from content_zavod.job_queue import JobId, JobQueue
from content_zavod.owner_settings import BotStateStore, WorkspaceSettingsStore
from content_zavod.scheduling import ScheduleSettings
from content_zavod.settings import OnboardingState, SettingsService
from content_zavod.telegram import (
    ArticleCommentPrompt,
    ArticleId,
    CommentGatedRegeneration,
    ExportArticle,
    HistoryVersion,
    HistoryVersions,
    HistoryWeek,
    Page,
    PlanId,
    PlanItemId,
    PlanReview,
    SimpleAction,
    TelegramCommentPrompt,
    TelegramGateway,
)
from content_zavod.telegram.asset_photos import AssetPhotos
from content_zavod.telegram.callback_codec import Action, CallbackPayload
from content_zavod.telegram.callback_dispatcher import CallbackDispatcher, CallbackInput
from content_zavod.telegram.guide import Guide
from content_zavod.telegram.input_prompt import InputPrompt
from content_zavod.telegram.main_menu import MainMenu
from content_zavod.telegram.onboarding import Onboarding
from content_zavod.telegram.pending_inputs import PendingInputs
from content_zavod.telegram.settings_screen import SettingsScreen
from content_zavod.telegram.style_samples import StyleSamples
from content_zavod.telegram.texts import STALE_BUTTON
from content_zavod.workspaces import Limits, TrialLimits, Workspace, WorkspaceId, Workspaces
from tests.tenancy import WS_A, WS_B

from .fakes import RecordingBot

WEEK = "2026-W42"
# Every table with B's data, dumped whole: any write by A's press would show.
_TABLES = (
    "plans",
    "plan_items",
    "articles",
    "article_versions",
    "jobs",
    "pending_inputs",
    "workspace_settings",
)


@dataclass(frozen=True)
class Content:
    plan_id: PlanId
    item_id: PlanItemId
    article_id: ArticleId
    version_id: int
    failed_job_id: int
    style_job_id: int


@pytest_asyncio.fixture(loop_scope="session")
async def queue(pool: asyncpg.Pool) -> AsyncIterator[JobQueue]:
    await pool.execute(
        "TRUNCATE TABLE jobs, article_versions, articles, plan_items, plans, generation_steps, "
        "pending_inputs RESTART IDENTITY CASCADE"
    )
    yield JobQueue(pool)


async def _content(pool: asyncpg.Pool, plan: Plan, article: Article, ws: WorkspaceId) -> Content:
    queue_ = JobQueue(pool)
    plan_id = await plan.add_topics(ws, WEEK, [TopicDraft(title="Тема")])
    item_id = (await plan.get(ws, plan_id)).items[0].id
    article_id = await article.request_generation(ws, plan_id, item_id, "Тема", "", [], "zen")
    job_id = await pool.fetchval(
        "SELECT active_generation_job_id FROM articles WHERE id = $1", article_id
    )
    version = GeneratedVersion(
        content="текст", prompt="p", model="m", tokens=1, cost=0.0, source_job_id=job_id
    )
    assert await article.record_version(ws, article_id, version) == "applied"
    version_id = (await article.list_versions(ws, article_id))[0].id
    await pool.execute(
        "UPDATE jobs SET status = 'failed', notified_at = now() WHERE id = $1", job_id
    )
    # A finished Профиль стиля analysis (#134), its profile waiting for «✅ Да, это я».
    style_job_id = await queue_.enqueue(ws, "analyze_style", {"message_id": 1}, "style")
    output = {
        "status": "ok",
        "profile": {"traits": {"tone": "сухой"}, "summary": "Вы пишете коротко."},
    }
    await pool.execute(
        "UPDATE jobs SET status = 'done', output = $2::jsonb, notified_at = now() WHERE id = $1",
        style_job_id,
        json.dumps(output),
    )
    return Content(plan_id, item_id, article_id, version_id, job_id, style_job_id)


async def _snapshot(pool: asyncpg.Pool, ws: WorkspaceId) -> dict[str, list[dict]]:
    return {
        table: [
            dict(row)
            for row in await pool.fetch(
                f"SELECT * FROM {table} WHERE workspace_id = $1 ORDER BY 1", ws
            )
        ]
        for table in _TABLES
    }


async def _no_download(file_id: str) -> bytes:
    raise AssertionError("no file here")


def _dispatcher(pool: asyncpg.Pool, queue: JobQueue, bot: RecordingBot) -> CallbackDispatcher:
    plan = Plan(pool, queue, covers_enabled=True)
    article = Article(pool, queue)
    gateway = TelegramGateway(bot, covers=True)
    pending = PendingInputs(pool)
    prompts = InputPrompt(bot, pending)
    settings = SettingsService(WorkspaceSettingsStore(pool))
    schedule = ScheduleSettings(pool)
    onboarding_state = OnboardingState(WorkspaceSettingsStore(pool))
    limits = Limits(pool, TrialLimits(plans=100, articles=100, jobs_per_day=100))
    menu = MainMenu(
        plan,
        queue,
        schedule,
        bot,
        gateway,
        prompts,
        settings=settings,
        onboarding=onboarding_state,
        limits=limits,
    )
    return CallbackDispatcher(
        plan,
        article,
        gateway,
        bot,
        PlanReview(plan, TelegramCommentPrompt(bot), pending),
        CommentGatedRegeneration[ArticleId](
            article.request_regeneration,
            ArticleCommentPrompt(bot, article),
            pending,
            kind="article_comment",
        ),
        SettingsScreen(settings, schedule, Workspaces(pool), bot, prompts),
        queue,
        menu,
        prompts,
        Onboarding(onboarding_state, settings, prompts, bot, menu, schedule),
        guide=Guide(AssetPhotos(bot, BotStateStore(pool)), bot),
        limits=limits,
        style_samples=StyleSamples(
            queue, settings, pending, prompts, bot, _no_download, onboarding_state, limits
        ),
    )


def _payloads_with_bs_ids(b: Content) -> list[CallbackPayload]:
    plan, item, article = b.plan_id, b.item_id, b.article_id
    return [
        Page(plan, 0),
        HistoryWeek(plan, 0),
        HistoryVersions(article, 0),
        HistoryVersion(article, b.version_id, 0),
        ExportArticle(article, "md"),
        SimpleAction("delete", item),
        SimpleAction("regenerate", item),
        SimpleAction("approve_all", plan),
        SimpleAction("regenerate_article", article),
        SimpleAction("request_cover", item),
        SimpleAction("approve", article),
        SimpleAction("confirm_regenerate_plan", plan),
        SimpleAction("cancel_regenerate_plan", plan),
        SimpleAction("retry", str(b.failed_job_id)),
        SimpleAction("hub_topic", item),
        SimpleAction("hub_back", plan),
        SimpleAction("hub_cover", item),
        SimpleAction("hub_article", article),
        SimpleAction("hub_approve", article),
        SimpleAction("hub_retry", plan),
        SimpleAction("hub_retry_topic", item),
        SimpleAction("style_ok", str(b.style_job_id)),
        SimpleAction("style_fix", str(b.style_job_id)),
    ]


# Действия whose id is not a План/Тема/Статья/Версия/Задача: a page number, a Настройка key,
# a screen's origin, a wizard step, a slide - or, for the two «Отмена», the presser's own wait
# (looked up in their own Пространство only, see `test_cancel_comment_with_bs_id_...`).
_ACTIONS_WITHOUT_A_DOMAIN_ID: set[Action] = {
    "history_page",
    "persona_template",
    "cancel_comment",
    "cancel_input",
    "menu",
    "menu_plan",
    "menu_generate_plan",
    "menu_topic",
    "menu_history",
    "settings",
    "edit_setting",
    "ask_setting",
    "pick_setting",
    "schedule",
    "schedule_day",
    "schedule_time",
    "schedule_tz",
    "schedule_tz_pick",
    "onboarding_step",
    "onboarding_pick",
    "onboarding_later",
    "onboarding_launch",
    "guide",
    "guide_slide",
    "style_start",
    "style_done",
    "style_presets",
}


def _action(payload: CallbackPayload) -> Action:
    if isinstance(payload, SimpleAction):
        return payload.action
    return {
        Page: "page",
        HistoryWeek: "history_week",
        HistoryVersions: "history_versions",
        HistoryVersion: "history_version",
        ExportArticle: "export_article",
    }[type(payload)]  # type: ignore[return-value]


def test_every_action_with_an_id_is_in_the_table() -> None:
    """A new Действие must be classified: either it carries an id (and joins the table) or
    it doesn't."""
    dummy = Content(PlanId("p"), PlanItemId("i"), ArticleId("a"), 1, 1, 1)
    with_ids = {_action(payload) for payload in _payloads_with_bs_ids(dummy)}

    assert with_ids | _ACTIONS_WITHOUT_A_DOMAIN_ID == set(get_args(Action))
    assert not with_ids & _ACTIONS_WITHOUT_A_DOMAIN_ID


class _Answerer:
    def __init__(self) -> None:
        self.calls: list[tuple[str | None, bool | None]] = []

    async def __call__(self, text: str | None = None, show_alert: bool | None = None) -> None:
        self.calls.append((text, show_alert))


@pytest.fixture
def workspace_a() -> Workspace:
    return Workspace(WS_A, WS_A, None, "Europe/Moscow", None, None)


async def test_a_press_by_a_with_bs_ids_is_a_stale_button_and_changes_nothing_of_b(
    pool: asyncpg.Pool, queue: JobQueue, workspace_a: Workspace
) -> None:
    plan, article = Plan(pool, queue, covers_enabled=True), Article(pool, queue)
    await _content(pool, plan, article, WS_A)
    theirs = await _content(pool, plan, article, WS_B)
    before = await _snapshot(pool, WS_B)
    mine_before = await _snapshot(pool, WS_A)

    for payload in _payloads_with_bs_ids(theirs):
        bot = RecordingBot()
        answer = _Answerer()

        await _dispatcher(pool, queue, bot).dispatch(
            workspace_a, CallbackInput(WS_A, 7, WS_A, None, payload), answer
        )

        assert answer.calls == [(STALE_BUTTON, True)], payload  # the only answer Telegram takes
        assert (bot.sent, bot.edited, bot.photos, bot.deleted) == ([], [], [], []), payload
        assert await _snapshot(pool, WS_B) == before, payload
    # A's press created nothing in A either: no comment prompt opened, no Задача queued.
    assert await _snapshot(pool, WS_A) == mine_before


async def test_the_same_presses_by_b_work(pool: asyncpg.Pool, queue: JobQueue) -> None:
    """The control: B's own ids aren't refused - the refusal above is about the Workspace."""
    plan, article = Plan(pool, queue, covers_enabled=True), Article(pool, queue)
    theirs = await _content(pool, plan, article, WS_B)
    workspace_b = Workspace(WS_B, WS_B, None, "Europe/Moscow", None, None)

    for payload in (
        Page(theirs.plan_id, 0),
        SimpleAction("regenerate", theirs.item_id),
        SimpleAction("retry", str(theirs.failed_job_id)),
        SimpleAction("style_ok", str(theirs.style_job_id)),
        SimpleAction("hub_article", theirs.article_id),
        SimpleAction("hub_approve", theirs.article_id),
        SimpleAction("regenerate_article", theirs.article_id),
    ):
        answer = _Answerer()
        await _dispatcher(pool, queue, RecordingBot()).dispatch(
            workspace_b, CallbackInput(WS_B, 7, WS_B, None, payload), answer
        )
        assert (STALE_BUTTON, True) not in answer.calls, payload
    assert await queue.get_status(WS_B, JobId(theirs.failed_job_id)) == "queued"
    assert (await article.get_summary(WS_B, theirs.article_id)).status == "exported"
    style = (await SettingsService(WorkspaceSettingsStore(pool)).read(WS_B)).style_profile
    assert style is not None and style.summary == "Вы пишете коротко."


async def test_cancel_comment_with_bs_id_drops_nothing_of_b(
    pool: asyncpg.Pool, queue: JobQueue, workspace_a: Workspace
) -> None:
    """The «Отмена» buttons only ever look at the presser's own wait - B's stays open."""
    plan, article = Plan(pool, queue, covers_enabled=True), Article(pool, queue)
    theirs = await _content(pool, plan, article, WS_B)
    workspace_b = Workspace(WS_B, WS_A, None, "Europe/Moscow", None, None)
    # B's wait under the very same chat and user ids as A's press.
    await _dispatcher(pool, queue, RecordingBot()).dispatch(
        workspace_b,
        CallbackInput(WS_A, 7, WS_A, None, SimpleAction("regenerate", theirs.item_id)),
        _Answerer(),
    )
    before = await _snapshot(pool, WS_B)
    bot = RecordingBot()

    await _dispatcher(pool, queue, bot).dispatch(
        workspace_a,
        CallbackInput(WS_A, 7, WS_A, None, SimpleAction("cancel_comment", theirs.item_id)),
        _Answerer(),
    )

    assert await _snapshot(pool, WS_B) == before
    assert len(before["pending_inputs"]) == 1
    assert bot.deleted == []
