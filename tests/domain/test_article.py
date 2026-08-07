import pytest

from content_zavod.domain import (
    Article,
    ArticleNotFound,
    ArticleNotReady,
    ArticleNotRegenerable,
    GeneratedVersion,
    Plan,
    PlanId,
    PlanItemId,
    TopicDraft,
)
from content_zavod.job_queue import JobQueue

_VERSION = GeneratedVersion(content="Hello, world.", prompt="write it", model="yandexgpt", tokens=42, cost=0.01)


async def _create_plan_item(plan: Plan) -> tuple[PlanId, PlanItemId]:
    plan_id = await plan.create("Week 1", [TopicDraft(title="Topic A")])
    view = await plan.get(plan_id)
    return plan_id, view.items[0].id


async def test_create_starts_queued_and_is_absent_from_get_until_a_version_exists(
    article: Article, plan: Plan
) -> None:
    plan_id, item_id = await _create_plan_item(plan)
    article_id = await article.create(plan_id, item_id, "Topic A", "zen")

    with pytest.raises(ArticleNotReady):
        await article.get(article_id)


async def test_get_raises_for_unknown_article(article: Article) -> None:
    with pytest.raises(ArticleNotFound):
        await article.get("missing")


async def test_record_version_makes_the_article_available_via_get(article: Article, plan: Plan) -> None:
    plan_id, item_id = await _create_plan_item(plan)
    article_id = await article.create(plan_id, item_id, "Topic A", "zen")

    await article.record_version(article_id, _VERSION)

    view = await article.get(article_id)
    assert view.title == "Topic A"
    assert view.platform == "zen"
    assert view.content == b"Hello, world."
    assert view.filename == "topic-a-zen.txt"


async def test_record_version_raises_for_unknown_article(article: Article) -> None:
    with pytest.raises(ArticleNotFound):
        await article.record_version("missing", _VERSION)


async def test_record_version_appends_rather_than_overwrites(article: Article, plan: Plan) -> None:
    plan_id, item_id = await _create_plan_item(plan)
    article_id = await article.create(plan_id, item_id, "Topic A", "zen")

    await article.record_version(article_id, _VERSION)
    second = GeneratedVersion(content="Second draft.", prompt="rewrite", model="yandexgpt", tokens=10, cost=0.02)
    await article.record_version(article_id, second)

    view = await article.get(article_id)
    assert view.content == b"Second draft."


async def test_list_for_plan_only_returns_articles_with_a_version(article: Article, plan: Plan) -> None:
    plan_id, item_id = await _create_plan_item(plan)
    ready_id = await article.create(plan_id, item_id, "Topic A", "zen")
    await article.create(plan_id, item_id, "Topic A", "vc")  # still queued, no version
    await article.record_version(ready_id, _VERSION)

    views = await article.list_for_plan(plan_id)

    assert [v.platform for v in views] == ["zen"]


async def test_request_regeneration_moves_ready_article_to_regenerating_and_enqueues_a_job(
    article: Article, plan: Plan, queue: JobQueue
) -> None:
    plan_id, item_id = await _create_plan_item(plan)
    article_id = await article.create(plan_id, item_id, "Topic A", "zen")
    await article.record_version(article_id, _VERSION)

    await article.request_regeneration(article_id, comment="shorter please")

    claimed = await queue.claim_next()
    assert claimed is not None
    assert claimed.job_type == "regenerate_article"
    assert claimed.payload == {"article_id": article_id, "comment": "shorter please"}


async def test_request_regeneration_is_idempotent_while_already_regenerating(
    article: Article, plan: Plan, queue: JobQueue
) -> None:
    plan_id, item_id = await _create_plan_item(plan)
    article_id = await article.create(plan_id, item_id, "Topic A", "zen")
    await article.record_version(article_id, _VERSION)

    await article.request_regeneration(article_id, comment="first")
    await article.request_regeneration(article_id, comment="second")  # no-op, must not raise/enqueue again

    first_claim = await queue.claim_next()
    assert first_claim is not None
    second_claim = await queue.claim_next()
    assert second_claim is None


async def test_request_regeneration_raises_while_still_queued(article: Article, plan: Plan) -> None:
    plan_id, item_id = await _create_plan_item(plan)
    article_id = await article.create(plan_id, item_id, "Topic A", "zen")

    with pytest.raises(ArticleNotRegenerable):
        await article.request_regeneration(article_id, comment=None)


async def test_mark_exported_requires_a_ready_article(article: Article, plan: Plan) -> None:
    plan_id, item_id = await _create_plan_item(plan)
    article_id = await article.create(plan_id, item_id, "Topic A", "zen")

    with pytest.raises(ArticleNotReady):
        await article.mark_exported(article_id)


async def test_mark_exported_is_idempotent(article: Article, plan: Plan) -> None:
    plan_id, item_id = await _create_plan_item(plan)
    article_id = await article.create(plan_id, item_id, "Topic A", "zen")
    await article.record_version(article_id, _VERSION)

    await article.mark_exported(article_id)
    await article.mark_exported(article_id)  # no-op, must not raise
