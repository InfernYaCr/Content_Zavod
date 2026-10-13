"""Cross-tenant isolation of the domain repositories and the Job Queue (ADR-0015, #118).

Workspaces A and B hold the same data (one week, one Тема, one Статья with a Версия). Acting
as A on any of B's ids must find nothing - the same "not found" as a missing id - and must
leave B exactly as it was.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import asyncpg
import pytest

from content_zavod.domain import (
    Article,
    ArticleNotFound,
    ArticleVersionNotFound,
    GeneratedVersion,
    Plan,
    PlanId,
    PlanItemId,
    PlanItemNotFound,
    PlanMessageRef,
    PlanNotFound,
    TopicDraft,
    TopicResearch,
    TopicResearchStore,
)
from content_zavod.domain.research import ResearchBundle
from content_zavod.domain.types import ArticleId
from content_zavod.job_queue import JobNotFound, JobQueue
from content_zavod.workspaces import WorkspaceId

WEEK = "2026-W42"


@dataclass(frozen=True)
class Content:
    plan_id: PlanId
    item_id: PlanItemId
    article_id: ArticleId
    version_id: int


async def _content(
    pool: asyncpg.Pool, plan: Plan, article: Article, workspace_id: WorkspaceId, title: str
) -> Content:
    plan_id = await plan.add_topics(workspace_id, WEEK, [TopicDraft(title=title)])
    item_id = (await plan.get(workspace_id, plan_id)).items[0].id
    article_id = await article.request_generation(
        workspace_id, plan_id, item_id, title, "", [], "zen"
    )
    job_id = await pool.fetchval(
        "SELECT active_generation_job_id FROM articles WHERE id = $1", article_id
    )
    version = GeneratedVersion(
        content="текст", prompt="p", model="m", tokens=1, cost=0.0, source_job_id=job_id
    )
    assert await article.record_version(workspace_id, article_id, version) == "applied"
    version_id = (await article.list_versions(workspace_id, article_id))[0].id
    return Content(plan_id, item_id, article_id, version_id)


async def _jobs_of(pool: asyncpg.Pool, workspace_id: WorkspaceId) -> int:
    return await pool.fetchval("SELECT count(*) FROM jobs WHERE workspace_id = $1", workspace_id)


async def test_the_same_week_gives_each_workspace_its_own_plan_and_job(
    pool: asyncpg.Pool,
    plan: Plan,
    article: Article,
    two_workspaces: tuple[WorkspaceId, WorkspaceId],
) -> None:
    a, b = two_workspaces
    content_a = await _content(pool, plan, article, a, "Тема")
    content_b = await _content(pool, plan, article, b, "Тема")

    # Two pending_review Plans for one week - one per Workspace, not one shared.
    assert content_a.plan_id != content_b.plan_id
    assert (await plan.find_active(a, WEEK)).id == content_a.plan_id
    assert (await plan.find_active(b, WEEK)).id == content_b.plan_id

    job_a = await plan.request_new(a, WEEK)
    job_b = await plan.request_new(b, WEEK)
    assert job_a != job_b
    assert await plan.request_new(a, WEEK) == job_a


async def test_plan_calls_with_another_workspaces_ids_find_nothing_and_change_nothing(
    pool: asyncpg.Pool,
    plan: Plan,
    article: Article,
    two_workspaces: tuple[WorkspaceId, WorkspaceId],
) -> None:
    a, b = two_workspaces
    await _content(pool, plan, article, a, "Тема")
    theirs = await _content(pool, plan, article, b, "Тема")
    jobs_before = await _jobs_of(pool, b)

    for call in (
        plan.get(a, theirs.plan_id),
        plan.get_hub(a, theirs.plan_id),
        plan.get_summary(a, theirs.plan_id),
        plan.get_message_ref(a, theirs.plan_id),
        plan.set_hub_view(a, theirs.plan_id, None),
        plan.approve_all(a, theirs.plan_id),
        plan.archive(a, theirs.plan_id),
        plan.request_replacement(a, theirs.plan_id),
    ):
        with pytest.raises(PlanNotFound):
            await call
    for call in (
        plan.get_item(a, theirs.item_id),
        plan.get_plan_id_for_item(a, theirs.item_id),
        plan.get_cover(a, theirs.item_id),
        plan.delete_item(a, theirs.item_id),
        plan.regenerate_item(a, theirs.item_id, "комментарий"),
        plan.request_cover(a, theirs.item_id),
        plan.apply_regeneration(a, theirs.item_id, TopicDraft(title="Чужая")),
    ):
        with pytest.raises(PlanItemNotFound):
            await call
    await plan.record_message_ref(a, theirs.plan_id, chat_id=1, message_id=2)
    assert not await plan.replace_message_ref(a, theirs.plan_id, PlanMessageRef(1, 2), 3, 4)
    await plan.apply_cover(a, theirs.item_id, b"png", "image/png")

    view = await plan.get(b, theirs.plan_id)
    assert [(item.title, item.status) for item in view.items] == [("Тема", "pending_review")]
    assert (await plan.get_summary(b, theirs.plan_id)).status == "pending_review"
    assert await plan.get_message_ref(b, theirs.plan_id) is None
    assert await plan.get_cover(b, theirs.item_id) is None
    assert await _jobs_of(pool, b) == jobs_before
    assert (await plan.list_page(a, page=0, page_size=10))[1] == 1


async def test_recent_topic_titles_never_include_another_workspaces_topics(
    pool: asyncpg.Pool,
    plan: Plan,
    article: Article,
    two_workspaces: tuple[WorkspaceId, WorkspaceId],
) -> None:
    a, b = two_workspaces
    await _content(pool, plan, article, a, "Тема A")
    await _content(pool, plan, article, b, "Тема B")
    since = datetime.now(UTC) - timedelta(days=1)

    assert await plan.recent_topic_titles(a, since) == ["Тема A"]
    assert await plan.recent_topic_titles(b, since) == ["Тема B"]


async def test_another_workspaces_cover_job_resolves_to_nothing(
    pool: asyncpg.Pool,
    plan: Plan,
    article: Article,
    two_workspaces: tuple[WorkspaceId, WorkspaceId],
) -> None:
    a, b = two_workspaces
    theirs = await _content(pool, plan, article, b, "Тема")
    await plan.request_cover(b, theirs.item_id, manual=True)
    job_id = await pool.fetchval(
        "SELECT active_cover_job_id FROM plan_items WHERE id = $1", theirs.item_id
    )

    assert await plan.mark_cover_generation_failed(a, job_id) is None
    assert await plan.is_manual_cover_job(a, job_id) is False
    assert await plan.is_manual_cover_job(b, job_id) is True
    assert await plan.mark_cover_generation_failed(b, job_id) == theirs.item_id


async def test_article_calls_with_another_workspaces_ids_find_nothing_and_change_nothing(
    pool: asyncpg.Pool,
    plan: Plan,
    article: Article,
    two_workspaces: tuple[WorkspaceId, WorkspaceId],
) -> None:
    a, b = two_workspaces
    await _content(pool, plan, article, a, "Тема")
    theirs = await _content(pool, plan, article, b, "Тема")
    await article.set_telegraph_path(b, theirs.article_id, "page-b")

    for call in (
        article.get(a, theirs.article_id),
        article.get_summary(a, theirs.article_id),
        article.get_plan_id(a, theirs.article_id),
        article.list_versions(a, theirs.article_id),
        article.get_telegraph_path(a, theirs.article_id),
        article.set_telegraph_path(a, theirs.article_id, "page-a"),
        article.clear_telegraph_path(a, theirs.article_id),
        article.request_regeneration(a, theirs.article_id, None),
        article.mark_exported(a, theirs.article_id),
        article.record_version(a, theirs.article_id, GeneratedVersion("чужой", "p", "m", 1, 0.0)),
    ):
        with pytest.raises(ArticleNotFound):
            await call
    with pytest.raises(ArticleVersionNotFound):
        await article.get_version(a, theirs.article_id, theirs.version_id)
    assert await article.list_for_plan(a, theirs.plan_id) == []
    assert await article.list_summary_for_plan(a, theirs.plan_id) == []

    assert (await article.get_summary(b, theirs.article_id)).status == "ready"
    assert await article.get_telegraph_path(b, theirs.article_id) == "page-b"
    assert len(await article.list_versions(b, theirs.article_id)) == 1


async def test_a_failed_job_of_another_workspace_marks_nothing(
    pool: asyncpg.Pool,
    plan: Plan,
    article: Article,
    two_workspaces: tuple[WorkspaceId, WorkspaceId],
) -> None:
    a, b = two_workspaces
    theirs = await _content(pool, plan, article, b, "Тема")
    await article.request_regeneration(b, theirs.article_id, None)
    job_id = await pool.fetchval(
        "SELECT active_generation_job_id FROM articles WHERE id = $1", theirs.article_id
    )

    assert await article.mark_generation_failed(a, job_id) is None
    assert (await article.get_summary(b, theirs.article_id)).status == "regenerating"


async def test_job_queue_keeps_workspaces_apart(
    pool: asyncpg.Pool, queue: JobQueue, two_workspaces: tuple[WorkspaceId, WorkspaceId]
) -> None:
    a, b = two_workspaces
    job_a = await queue.enqueue(a, "generate_plan", {}, "generate_plan:2026-W42")
    job_b = await queue.enqueue(b, "generate_plan", {}, "generate_plan:2026-W42")
    assert job_a != job_b
    await pool.execute("UPDATE jobs SET status = 'failed' WHERE id = $1", job_b)

    with pytest.raises(JobNotFound):
        await queue.retry(a, job_b)
    with pytest.raises(JobNotFound):
        await queue.get_status(a, job_b)
    assert await queue.get_job(a, job_b) is None

    assert await queue.get_status(b, job_b) == "failed"
    assert await queue.retry(b, job_b) is True


async def test_topic_research_is_per_workspace(
    pool: asyncpg.Pool,
    plan: Plan,
    article: Article,
    two_workspaces: tuple[WorkspaceId, WorkspaceId],
) -> None:
    a, b = two_workspaces
    theirs = await _content(pool, plan, article, b, "Тема")
    store = TopicResearchStore(pool)
    research = TopicResearch(bundle=ResearchBundle(query="q", status="no_evidence"), outline="o")
    # A's write can't land on B's Тема: the composite FK refuses a new row...
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await store.put(a, theirs.item_id, "fp", research)
    await store.put(b, theirs.item_id, "fp", research)

    assert await store.get(a, theirs.item_id, "fp") is None
    # ...and the upsert never overwrites B's existing one.
    await store.put(a, theirs.item_id, "fp-a", TopicResearch(research.bundle, "чужой"))
    assert await store.get(b, theirs.item_id, "fp") == research
