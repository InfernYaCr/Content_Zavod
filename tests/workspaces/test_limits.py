"""The Лимит on a real database (#118 PR 5, ADR-0015 «Лимит», «Защита от лавины»): the check
before enqueueing, the charge when a result is applied, the race between two presses, and
that one Пространство's Лимит is its own."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import asyncpg
import pytest
import pytest_asyncio

from content_zavod.domain import PLATFORMS, Article, GeneratedVersion, Plan, PlanId, TopicDraft
from content_zavod.job_queue import JobId, JobQueue
from content_zavod.telegram.callback_dispatcher import approve_and_generate
from content_zavod.telegram.generate_plan_command import (
    handle_confirm_regenerate_plan,
    handle_generate_plan_command,
)
from content_zavod.workspaces import (
    DailyFreeJobLimitReached,
    DailyJobLimitReached,
    DailyPlanLimitReached,
    GenerationBusy,
    Limits,
    TrialLimitExhausted,
    TrialLimits,
    Workspace,
    WorkspaceId,
)
from content_zavod.workspaces.admin import WorkspaceAdmin
from tests.tenancy import WS_A, WS_B

TRIAL = TrialLimits(plans=1, articles=8, jobs_per_day=50, contact="@owner")
TOPICS = [TopicDraft(title=f"Тема {n}", summary="s", keywords=["k"]) for n in range(3)]


@pytest_asyncio.fixture(autouse=True)
async def _clean(pool: asyncpg.Pool) -> AsyncIterator[None]:
    await pool.execute(
        "TRUNCATE TABLE jobs, article_versions, articles, plan_items, plans, generation_steps, "
        "limit_charges CASCADE"
    )
    await pool.execute(
        "UPDATE workspaces SET plan_limit = NULL, article_limit = NULL WHERE id = ANY($1::bigint[])",
        [WS_A, WS_B],
    )
    yield


@pytest.fixture
def queue(pool: asyncpg.Pool) -> JobQueue:
    return JobQueue(pool)


@pytest.fixture
def plan(pool: asyncpg.Pool, queue: JobQueue) -> Plan:
    return Plan(pool, queue)


@pytest.fixture
def article(pool: asyncpg.Pool, queue: JobQueue) -> Article:
    return Article(pool, queue)


@pytest.fixture
def limits(pool: asyncpg.Pool) -> Limits:
    return Limits(pool, TRIAL)


async def _all_notified(pool: asyncpg.Pool, workspace_id: WorkspaceId) -> None:
    """Every Job of the Workspace finished and its notification handled."""
    await pool.execute(
        "UPDATE jobs SET status = 'done', notified_at = now() WHERE workspace_id = $1",
        workspace_id,
    )


async def _jobs(pool: asyncpg.Pool, workspace_id: WorkspaceId, job_type: str) -> list[int]:
    rows = await pool.fetch(
        "SELECT id FROM jobs WHERE workspace_id = $1 AND job_type = $2 ORDER BY id",
        workspace_id,
        job_type,
    )
    return [row["id"] for row in rows]


async def _write(article: Article, workspace_id: WorkspaceId, article_id: str, job_id: int) -> str:
    """Apply a finished generation, as the notification handler does."""
    return await article.record_version(
        workspace_id,
        article_id,  # type: ignore[arg-type]
        GeneratedVersion(
            content="текст", prompt="p", model="m", tokens=1, cost=None, source_job_id=job_id
        ),
    )


async def _article_jobs(pool: asyncpg.Pool, workspace_id: WorkspaceId) -> list[tuple[str, int]]:
    rows = await pool.fetch(
        "SELECT id, active_generation_job_id FROM articles WHERE workspace_id = $1 ORDER BY id",
        workspace_id,
    )
    return [(row["id"], row["active_generation_job_id"]) for row in rows]


async def test_the_trial_gives_one_plan_and_eight_statyi_counting_refinements(
    pool: asyncpg.Pool, plan: Plan, article: Article, limits: Limits
) -> None:
    """The done criterion: 1/8 - one Plan, its six Статьи and two «✏️ Доработать»; the ninth
    Статья and the second Plan are refused with the contact; `/admin limit` opens them again."""
    async with limits.reserve(WS_A, "generate_plan") as reservation:
        plan_job = await plan.request_new(WS_A, "2026-W42", conn=reservation.conn)
    plan_id = await plan.add_topics(WS_A, "2026-W42", TOPICS, source_job_id=plan_job)
    await _all_notified(pool, WS_A)

    await approve_and_generate(limits, plan, article, WS_A, plan_id)
    for article_id, job_id in await _article_jobs(pool, WS_A):
        assert await _write(article, WS_A, article_id, job_id) == "applied"
    await _all_notified(pool, WS_A)
    refined = [article_id for article_id, _ in await _article_jobs(pool, WS_A)][:2]
    for article_id in refined:
        async with limits.reserve(WS_A, "regenerate_article") as reservation:
            await article.request_regeneration(WS_A, article_id, None, conn=reservation.conn)
        [(_, job_id)] = [row for row in await _article_jobs(pool, WS_A) if row[0] == article_id]
        assert await _write(article, WS_A, article_id, job_id) == "applied"
        await _all_notified(pool, WS_A)

    usage = await limits.usage(WS_A)
    assert (usage.plans_used, usage.articles_used) == (1, 8)
    with pytest.raises(TrialLimitExhausted) as ninth:
        async with limits.reserve(WS_A, "regenerate_article"):
            pass
    assert ninth.value.contact == "@owner"
    with pytest.raises(TrialLimitExhausted):
        async with limits.reserve(WS_A, "generate_plan"):
            pass

    assert await WorkspaceAdmin(pool, TRIAL).set_limits(1, WS_A, 2, 12)
    async with limits.reserve(WS_A, "generate_plan") as reservation:
        reservation.require("generate_article", 4)


async def test_two_simultaneous_approvals_with_room_for_one_set_enqueue_exactly_one(
    pool: asyncpg.Pool, plan: Plan, article: Article, limits: Limits
) -> None:
    first = await plan.add_topics(WS_A, "2026-W42", TOPICS)
    second = await plan.add_topics(WS_A, "2026-W43", TOPICS)

    results = await asyncio.gather(
        approve_and_generate(limits, plan, article, WS_A, first),
        approve_and_generate(limits, plan, article, WS_A, second),
        return_exceptions=True,
    )

    refused = [r for r in results if isinstance(r, Exception)]
    assert len(refused) == 1 and isinstance(refused[0], GenerationBusy | TrialLimitExhausted)
    assert len(await _jobs(pool, WS_A, "generate_article")) == len(TOPICS) * len(PLATFORMS)
    statuses = {
        row["status"]
        for row in await pool.fetch("SELECT status FROM plans WHERE workspace_id = $1", WS_A)
    }
    assert statuses == {"approved", "pending_review"}  # the refused one is left as it was


async def test_while_a_job_is_unfinished_nothing_new_starts(
    pool: asyncpg.Pool, plan: Plan, limits: Limits, queue: JobQueue
) -> None:
    """«Дождитесь…»: queued, running, and done-but-not-yet-delivered all count."""
    await queue.enqueue(WS_A, "generate_cover", {}, "cover:1")
    for status in ("queued", "running", "done"):
        await pool.execute("UPDATE jobs SET status = $2 WHERE workspace_id = $1", WS_A, status)
        with pytest.raises(GenerationBusy):
            async with limits.reserve(WS_A, "generate_plan") as reservation:
                await plan.request_new(WS_A, "2026-W42", conn=reservation.conn)

    assert await _jobs(pool, WS_A, "generate_plan") == []
    await _all_notified(pool, WS_A)
    async with limits.reserve(WS_A, "generate_plan") as reservation:
        await plan.request_new(WS_A, "2026-W42", conn=reservation.conn)


async def test_a_repeated_approve_all_of_an_approved_plan_counts_nothing_again(
    pool: asyncpg.Pool, plan: Plan, article: Article, limits: Limits
) -> None:
    plan_id = await plan.add_topics(WS_A, "2026-W42", TOPICS)
    await approve_and_generate(limits, plan, article, WS_A, plan_id)
    await _all_notified(pool, WS_A)
    # Short of a second set: a recount of the six would be refused.
    await pool.execute("UPDATE workspaces SET article_limit = 7 WHERE id = $1", WS_A)

    await approve_and_generate(limits, plan, article, WS_A, plan_id)

    assert len(await _jobs(pool, WS_A, "generate_article")) == len(TOPICS) * len(PLATFORMS)


async def test_approve_all_short_of_the_limit_enqueues_nothing_and_says_whats_left(
    pool: asyncpg.Pool, plan: Plan, article: Article, limits: Limits
) -> None:
    await pool.execute("UPDATE workspaces SET article_limit = 4 WHERE id = $1", WS_A)
    plan_id = await plan.add_topics(WS_A, "2026-W42", TOPICS)

    with pytest.raises(TrialLimitExhausted) as refused:
        await approve_and_generate(limits, plan, article, WS_A, plan_id)

    assert (refused.value.usage.articles_left, refused.value.articles) == (4, 6)
    assert await pool.fetchval("SELECT count(*) FROM jobs WHERE workspace_id = $1", WS_A) == 0
    assert await pool.fetchval("SELECT count(*) FROM articles WHERE workspace_id = $1", WS_A) == 0
    assert (await plan.get_summary(WS_A, plan_id)).status == "pending_review"


async def test_a_failed_job_charges_nothing_and_its_retry_charges_once(
    pool: asyncpg.Pool, plan: Plan, article: Article, limits: Limits, queue: JobQueue
) -> None:
    plan_id = await plan.add_topics(WS_A, "2026-W42", TOPICS[:1])
    await approve_and_generate(limits, plan, article, WS_A, plan_id)
    article_id, job_id = (await _article_jobs(pool, WS_A))[0]
    # The Статья's Job failed (and its failure was delivered); the cover's succeeded.
    await _all_notified(pool, WS_A)
    await pool.execute("UPDATE jobs SET status = 'failed' WHERE id = $1", job_id)
    await article.mark_generation_failed(WS_A, job_id)
    assert (await limits.usage(WS_A)).articles_used == 0

    async with limits.reserve(WS_A, "regenerate_article") as reservation:
        assert await queue.retry(WS_A, JobId(job_id), conn=reservation.conn)
    assert await _write(article, WS_A, article_id, job_id) == "applied"
    # A redelivered notification of the same Job.
    assert await _write(article, WS_A, article_id, job_id) == "already_applied"

    assert (await limits.usage(WS_A)).articles_used == 1


async def test_a_stale_result_charges_nothing(
    pool: asyncpg.Pool, plan: Plan, article: Article, limits: Limits, queue: JobQueue
) -> None:
    plan_id = await plan.add_topics(WS_A, "2026-W42", TOPICS[:1])
    await approve_and_generate(limits, plan, article, WS_A, plan_id)
    article_id, _ = (await _article_jobs(pool, WS_A))[0]
    superseded = await queue.enqueue(WS_A, "regenerate_article", {}, "old-regeneration")

    assert await _write(article, WS_A, article_id, superseded) == "stale"
    assert (await limits.usage(WS_A)).articles_used == 0


async def test_a_redelivered_plan_result_charges_once(plan: Plan, limits: Limits) -> None:
    job_id = await plan.request_new(WS_A, "2026-W42")
    await plan.add_topics(WS_A, "2026-W42", TOPICS, source_job_id=job_id)
    await plan.add_topics(WS_A, "2026-W42", TOPICS, source_job_id=job_id)

    assert (await limits.usage(WS_A)).plans_used == 1


class _Notices:
    def __init__(self) -> None:
        self.texts: list[str] = []

    async def send_notice(self, chat_id: int, text: str) -> None:
        self.texts.append(text)

    async def edit_notice(self, chat_id: int, message_id: int, text: str) -> None:
        self.texts.append(text)


class _NicheSaved:
    async def read(self, workspace_id: WorkspaceId) -> object:
        return type("Settings", (), {"niche_saved": True})()


async def test_a_replacement_or_a_rerun_after_the_trial_plan_is_refused_with_the_contact(
    pool: asyncpg.Pool, plan: Plan, limits: Limits, queue: JobQueue
) -> None:
    """Every `generate_plan` run is a Plan of the Лимит - «Перегенерировать» (`replace:`) and
    the `rerun:` chain of «🪄 Составить План» too: each may rebuild the Niche Map (#140)."""
    async with limits.reserve(WS_A, "generate_plan") as reservation:
        job_id = await plan.request_new(WS_A, "2026-W42", conn=reservation.conn)
    plan_id = await plan.add_topics(WS_A, "2026-W42", TOPICS, source_job_id=job_id)
    await _all_notified(pool, WS_A)
    notices = _Notices()
    contact = "Чтобы продолжить, напишите @owner."

    await handle_confirm_regenerate_plan(
        plan,
        notices,
        limits,
        WS_A,
        1,
        5,
        plan_id,  # type: ignore[arg-type]
    )
    assert (await plan.get_summary(WS_A, plan_id)).status == "pending_review"

    await plan.archive(WS_A, plan_id)  # no active Plan: the week's done Job gets a rerun
    workspace = Workspace(WS_A, WS_A, None, "Europe/Moscow", None, None)
    await handle_generate_plan_command(
        plan,
        notices,  # type: ignore[arg-type]
        workspace,
        1,
        queue=queue,
        settings=_NicheSaved(),  # type: ignore[arg-type]
        limits=limits,
        now=lambda: datetime(2026, 10, 14, 9, 0, tzinfo=UTC),  # 2026-W42
    )

    assert len(notices.texts) == 2 and all(text.endswith(contact) for text in notices.texts)
    assert await _jobs(pool, WS_A, "generate_plan") == [job_id]


async def test_the_daily_ceiling_stops_new_jobs(pool: asyncpg.Pool, queue: JobQueue) -> None:
    limits = Limits(pool, TrialLimits(jobs_per_day=2))
    for n in range(2):
        await queue.enqueue(WS_A, "regenerate_topic", {}, f"topic:{n}")
    await _all_notified(pool, WS_A)

    with pytest.raises(DailyJobLimitReached):
        async with limits.reserve(WS_A):
            pass
    async with limits.reserve(WS_B):  # B's day is its own
        pass


async def test_jobs_free_of_the_limit_have_their_own_smaller_daily_cap(
    pool: asyncpg.Pool, queue: JobQueue
) -> None:
    """A Тема regeneration or a cover costs no Лимит, so «🔄» in a loop is braked by
    `FREE_JOBS_PER_DAY_LIMIT`, not only by the total cap."""
    limits = Limits(pool, TrialLimits(jobs_per_day=30, free_jobs_per_day=2))
    await queue.enqueue(WS_A, "regenerate_topic", {}, "topic:1")
    await queue.enqueue(WS_A, "generate_cover", {}, "cover:1")
    await _all_notified(pool, WS_A)

    for job_type in ("regenerate_topic", "generate_cover", "analyze_style"):
        with pytest.raises(DailyFreeJobLimitReached):
            async with limits.reserve(WS_A, job_type):
                pass
    async with limits.reserve(WS_A, "regenerate_article"):  # a Статья is braked by the Лимит
        pass


async def test_plan_generations_have_their_own_daily_cap(
    pool: asyncpg.Pool, plan: Plan, queue: JobQueue
) -> None:
    limits = Limits(pool, TrialLimits(plans=100, plan_jobs_per_day=2))
    for week in ("2026-W40", "2026-W41"):
        await plan.request_new(WS_A, week)
    await _all_notified(pool, WS_A)

    with pytest.raises(DailyPlanLimitReached):
        async with limits.reserve(WS_A, "generate_plan"):
            pass
    async with limits.reserve(WS_A, "regenerate_topic"):
        pass


async def test_one_reservation_adds_up_what_it_asked_for(pool: asyncpg.Pool) -> None:
    limits = Limits(pool, TrialLimits(articles=8, free_jobs_per_day=3))

    async with limits.reserve(WS_A) as reservation:
        reservation.require("generate_article", 6)
        reservation.require("generate_cover", 3)
        with pytest.raises(TrialLimitExhausted):
            reservation.require("regenerate_article", 3)
        with pytest.raises(DailyFreeJobLimitReached):
            reservation.require("generate_cover")


async def test_admin_limit_can_go_back_to_the_config(pool: asyncpg.Pool, limits: Limits) -> None:
    admin = WorkspaceAdmin(pool, TRIAL)
    assert await admin.set_limits(1, WS_A, 3, 30)

    assert await admin.set_limits(1, WS_A, None, None)

    usage = await limits.usage(WS_A)
    assert (usage.plan_limit, usage.article_limit) == (TRIAL.plans, TRIAL.articles)


async def test_charges_and_limits_of_a_dont_touch_b(
    pool: asyncpg.Pool, plan: Plan, limits: Limits
) -> None:
    for workspace_id in (WS_A, WS_B):
        await plan.add_topics(workspace_id, "2026-W42", TOPICS)
    job_id = await plan.request_new(WS_A, "2026-W42")
    await plan.add_topics(WS_A, "2026-W42", TOPICS, source_job_id=job_id)

    assert await WorkspaceAdmin(pool, TRIAL).set_limits(1, WS_A, 5, 50)

    a, b = await limits.usage(WS_A), await limits.usage(WS_B)
    assert (a.plans_used, a.plan_limit, a.article_limit) == (1, 5, 50)
    assert (b.plans_used, b.plan_limit, b.article_limit) == (0, 1, 8)
    async with limits.reserve(WS_B, "generate_plan"):  # B's busy-check doesn't see A's Jobs either
        pass


async def test_the_admin_overview_carries_no_content(pool: asyncpg.Pool, plan: Plan) -> None:
    await plan.add_topics(WS_A, "2026-W42", [TopicDraft(title="Секретная Тема", summary="s")])
    await pool.execute("UPDATE workspaces SET plan_limit = 3 WHERE id = $1", WS_A)

    overview, total = await WorkspaceAdmin(pool, TRIAL).overview(limit=50)

    assert total >= 2
    [a] = [workspace for workspace in overview if workspace.id == WS_A]
    assert (a.usage.plan_limit, a.usage.article_limit, a.custom_limit) == (3, 8, True)
    assert "Секретная" not in repr(overview)


async def test_set_limits_of_an_unknown_workspace_is_false(pool: asyncpg.Pool) -> None:
    assert not await WorkspaceAdmin(pool, TRIAL).set_limits(1, WorkspaceId(123_456_789), 1, 1)


async def test_reserve_ignores_a_plan_of_another_workspace(
    plan: Plan, article: Article, limits: Limits
) -> None:
    """Cross-tenant: A approving B's Plan by id finds nothing and enqueues nothing."""
    theirs = await plan.add_topics(WS_B, "2026-W42", TOPICS)

    with pytest.raises(Exception, match="No plan"):
        await approve_and_generate(limits, plan, article, WS_A, PlanId(theirs))

    assert (await plan.get_summary(WS_B, theirs)).status == "pending_review"
