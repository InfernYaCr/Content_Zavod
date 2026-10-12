"""The `regenerate_*` Job Handlers read only the claimed Job's own Пространство (ADR-0015).

A and B hold the same Тема and Статья. A Job of A whose payload names B's id must fail as
"not found" before anything of B reaches a prompt; the same Job as B succeeds, so it is the
Workspace that refuses, not a broken id.
"""

from __future__ import annotations

from dataclasses import dataclass

import asyncpg
import pytest

from content_zavod.domain import (
    Article,
    ArticleNotFound,
    GeneratedVersion,
    Plan,
    PlanItemId,
    PlanItemNotFound,
    TopicDraft,
    TopicResearchStore,
)
from content_zavod.domain.types import ArticleId
from content_zavod.owner_settings import WorkspaceSettingsStore
from content_zavod.pipelines import (
    HttpxPageFetcher,
    TopicResearcher,
    make_regenerate_article_handler,
    make_regenerate_topic_handler,
)
from content_zavod.settings import WorkspaceSettingsReader
from content_zavod.workspaces import WorkspaceId
from content_zavod.yandex import Completion, Message
from tests.tenancy import WS_A, WS_B


class RecordingTextGenerator:
    def __init__(self) -> None:
        self.calls: list[list[Message]] = []

    async def complete_with_usage(
        self, messages: list[Message], *, temperature: float = 0.7
    ) -> Completion:
        self.calls.append(messages)
        return Completion(
            text="Title: T\nSummary: s\nKeywords: k", model="yandexgpt/latest", tokens=1
        )


@dataclass(frozen=True)
class Content:
    item_id: PlanItemId
    article_id: ArticleId


async def _content(
    pool: asyncpg.Pool, plan: Plan, article: Article, workspace_id: WorkspaceId
) -> Content:
    plan_id = await plan.add_topics(workspace_id, "2026-W42", [TopicDraft(title="Тема")])
    item_id = (await plan.get(workspace_id, plan_id)).items[0].id
    article_id = await article.request_generation(
        workspace_id, plan_id, item_id, "Тема", "", [], "zen"
    )
    job_id = await pool.fetchval(
        "SELECT active_generation_job_id FROM articles WHERE id = $1", article_id
    )
    version = GeneratedVersion(
        content="текст", prompt="p", model="m", tokens=1, cost=0.0, source_job_id=job_id
    )
    assert await article.record_version(workspace_id, article_id, version) == "applied"
    return Content(item_id, article_id)


async def test_regenerate_topic_of_a_finds_no_topic_of_b(
    pool: asyncpg.Pool, plan: Plan, article: Article
) -> None:
    await _content(pool, plan, article, WS_A)
    b = await _content(pool, plan, article, WS_B)
    text_generator = RecordingTextGenerator()
    handler = make_regenerate_topic_handler(
        plan, text_generator, WorkspaceSettingsReader(WorkspaceSettingsStore(pool))
    )
    payload = {"plan_item_id": b.item_id, "comment": None}

    with pytest.raises(PlanItemNotFound):
        await handler(WS_A, payload)
    assert text_generator.calls == []

    assert (await handler(WS_B, payload))["plan_item_id"] == b.item_id


async def test_regenerate_article_of_a_finds_no_article_of_b(
    pool: asyncpg.Pool, plan: Plan, article: Article
) -> None:
    await _content(pool, plan, article, WS_A)
    b = await _content(pool, plan, article, WS_B)
    text_generator = RecordingTextGenerator()
    page_fetcher = HttpxPageFetcher()
    handler = make_regenerate_article_handler(
        article,
        plan,
        text_generator,
        TopicResearcher(None, page_fetcher, TopicResearchStore(pool)),
        WorkspaceSettingsReader(WorkspaceSettingsStore(pool)),
    )
    payload = {"article_id": b.article_id, "comment": None}

    try:
        with pytest.raises(ArticleNotFound):
            await handler(WS_A, payload)
        assert text_generator.calls == []

        assert (await handler(WS_B, payload))["article_id"] == b.article_id
    finally:
        await page_fetcher.aclose()
