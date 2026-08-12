import pytest

from content_zavod.domain import (
    Article,
    ArticleNotFound,
    ArticleNotReady,
    ArticleNotRegenerable,
    ArticleSummary,
    ArticleVersionNotFound,
    GeneratedVersion,
    Plan,
    PlanId,
    PlanItemId,
    TopicDraft,
)
from content_zavod.job_queue import JobQueue

_VERSION = GeneratedVersion(
    content="Hello, world.", prompt="write it", model="yandexgpt", tokens=42, cost=0.01
)


async def _create_plan_item(plan: Plan) -> tuple[PlanId, PlanItemId]:
    plan_id = await plan.add_topics("Week 1", [TopicDraft(title="Topic A")])
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


async def test_record_version_makes_the_article_available_via_get(
    article: Article, plan: Plan
) -> None:
    plan_id, item_id = await _create_plan_item(plan)
    article_id = await article.create(plan_id, item_id, "Topic A", "zen")

    await article.record_version(article_id, _VERSION)

    view = await article.get(article_id)
    assert view.title == "Topic A"
    assert view.platform == "zen"
    assert view.content == b"Hello, world."


async def test_record_version_raises_for_unknown_article(article: Article) -> None:
    with pytest.raises(ArticleNotFound):
        await article.record_version("missing", _VERSION)


async def test_record_version_appends_rather_than_overwrites(article: Article, plan: Plan) -> None:
    plan_id, item_id = await _create_plan_item(plan)
    article_id = await article.create(plan_id, item_id, "Topic A", "zen")

    await article.record_version(article_id, _VERSION)
    second = GeneratedVersion(
        content="Second draft.", prompt="rewrite", model="yandexgpt", tokens=10, cost=0.02
    )
    await article.record_version(article_id, second)

    view = await article.get(article_id)
    assert view.content == b"Second draft."


async def test_record_version_is_idempotent_for_the_same_source_job(
    article: Article, plan: Plan, queue: JobQueue
) -> None:
    plan_id, item_id = await _create_plan_item(plan)
    article_id = await article.request_generation(plan_id, item_id, "Topic A", "s", [], "zen")
    claimed = await queue.claim_next()
    assert claimed is not None
    version = GeneratedVersion(
        content="First draft.",
        prompt="write",
        model="yandexgpt",
        tokens=10,
        cost=0.01,
        source_job_id=claimed.id,
    )

    first = await article.record_version(article_id, version)
    replay = await article.record_version(article_id, version)

    assert first == "applied"
    assert replay == "already_applied"
    assert len(await article.list_versions(article_id)) == 1


async def test_stale_generation_result_cannot_replace_a_newer_version(
    article: Article, plan: Plan, queue: JobQueue
) -> None:
    plan_id, item_id = await _create_plan_item(plan)
    article_id = await article.request_generation(plan_id, item_id, "Topic A", "s", [], "zen")
    old_job = await queue.claim_next()
    assert old_job is not None
    await article.mark_generation_failed(old_job.id)
    await article.request_regeneration(article_id, comment="retry")
    new_job = await queue.claim_next()
    assert new_job is not None
    await article.record_version(
        article_id,
        GeneratedVersion("New", "p2", "m", 2, 0.0, source_job_id=new_job.id),
    )

    application = await article.record_version(
        article_id,
        GeneratedVersion("Old", "p1", "m", 1, 0.0, source_job_id=old_job.id),
    )

    assert application == "stale"
    assert (await article.get(article_id)).content == b"New"


async def test_final_generation_failure_marks_only_its_current_article_as_error(
    article: Article, plan: Plan, queue: JobQueue
) -> None:
    plan_id, item_id = await _create_plan_item(plan)
    article_id = await article.request_generation(plan_id, item_id, "Topic A", "s", [], "zen")
    claimed = await queue.claim_next()
    assert claimed is not None

    assert await article.mark_generation_failed(claimed.id) == article_id
    assert (await article.get_summary(article_id)).status == "error"
    assert await article.mark_generation_failed(claimed.id) == article_id
    assert await article.mark_generation_failed(claimed.id + 1000) is None


async def test_list_for_plan_only_returns_articles_with_a_version(
    article: Article, plan: Plan
) -> None:
    plan_id, item_id = await _create_plan_item(plan)
    ready_id = await article.create(plan_id, item_id, "Topic A", "zen")
    await article.create(plan_id, item_id, "Topic A", "vc")  # still queued, no version
    await article.record_version(ready_id, _VERSION)

    views = await article.list_for_plan(plan_id)

    assert [v.platform for v in views] == ["zen"]


async def test_list_summary_for_plan_includes_articles_without_a_version_yet(
    article: Article, plan: Plan
) -> None:
    plan_id, item_id = await _create_plan_item(plan)
    ready_id = await article.create(plan_id, item_id, "Topic A", "zen")
    queued_id = await article.create(plan_id, item_id, "Topic A", "vc")  # still queued, no version
    await article.record_version(ready_id, _VERSION)

    summaries = await article.list_summary_for_plan(plan_id)

    assert {(s.id, s.platform, s.status) for s in summaries} == {
        (ready_id, "zen", "ready"),
        (queued_id, "vc", "queued"),
    }


async def test_list_summary_for_plan_reflects_current_status(article: Article, plan: Plan) -> None:
    plan_id, item_id = await _create_plan_item(plan)
    article_id = await article.create(plan_id, item_id, "Topic A", "zen")
    await article.record_version(article_id, _VERSION)

    await article.mark_exported(article_id)

    summaries = await article.list_summary_for_plan(plan_id)
    assert summaries == [
        ArticleSummary(id=article_id, title="Topic A", platform="zen", status="exported")
    ]


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
    await article.request_regeneration(
        article_id, comment="second"
    )  # no-op, must not raise/enqueue again

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


async def test_create_is_idempotent_on_plan_item_and_platform(article: Article, plan: Plan) -> None:
    plan_id, item_id = await _create_plan_item(plan)
    first_id = await article.create(plan_id, item_id, "Topic A", "zen")

    second_id = await article.create(plan_id, item_id, "Topic A", "zen")

    assert first_id == second_id


async def test_get_summary_returns_header_regardless_of_version_state(
    article: Article, plan: Plan
) -> None:
    plan_id, item_id = await _create_plan_item(plan)
    article_id = await article.create(plan_id, item_id, "Topic A", "zen")

    summary = await article.get_summary(article_id)

    assert summary == ArticleSummary(
        id=article_id, title="Topic A", platform="zen", status="queued"
    )


async def test_get_summary_raises_for_unknown_article(article: Article) -> None:
    with pytest.raises(ArticleNotFound):
        await article.get_summary("missing")


async def test_get_plan_id_returns_the_owning_plan(article: Article, plan: Plan) -> None:
    plan_id, item_id = await _create_plan_item(plan)
    article_id = await article.create(plan_id, item_id, "Topic A", "zen")

    assert await article.get_plan_id(article_id) == plan_id


async def test_get_plan_id_raises_for_unknown_article(article: Article) -> None:
    with pytest.raises(ArticleNotFound):
        await article.get_plan_id("missing")


async def test_list_versions_is_empty_for_an_article_with_no_version_yet(
    article: Article, plan: Plan
) -> None:
    plan_id, item_id = await _create_plan_item(plan)
    article_id = await article.create(plan_id, item_id, "Topic A", "zen")

    assert await article.list_versions(article_id) == []


async def test_list_versions_raises_for_unknown_article(article: Article) -> None:
    with pytest.raises(ArticleNotFound):
        await article.list_versions("missing")


async def test_list_versions_lists_every_version_newest_first_without_content(
    article: Article, plan: Plan
) -> None:
    plan_id, item_id = await _create_plan_item(plan)
    article_id = await article.create(plan_id, item_id, "Topic A", "zen")
    await article.record_version(article_id, _VERSION)
    second = GeneratedVersion(
        content="Second draft.", prompt="rewrite", model="yandexgpt-2", tokens=10, cost=0.02
    )
    await article.record_version(article_id, second)

    versions = await article.list_versions(article_id)

    assert [v.model for v in versions] == ["yandexgpt-2", "yandexgpt"]
    assert [v.tokens for v in versions] == [10, 42]
    assert not hasattr(versions[0], "content")


async def test_get_version_returns_the_requested_versions_full_content(
    article: Article, plan: Plan
) -> None:
    plan_id, item_id = await _create_plan_item(plan)
    article_id = await article.create(plan_id, item_id, "Topic A", "zen")
    await article.record_version(article_id, _VERSION)
    versions = await article.list_versions(article_id)

    view = await article.get_version(article_id, versions[0].id)

    assert view.content == "Hello, world."
    assert view.model == "yandexgpt"
    assert view.tokens == 42


async def test_get_version_raises_for_unknown_version_id(article: Article, plan: Plan) -> None:
    plan_id, item_id = await _create_plan_item(plan)
    article_id = await article.create(plan_id, item_id, "Topic A", "zen")
    await article.record_version(article_id, _VERSION)

    with pytest.raises(ArticleVersionNotFound):
        await article.get_version(article_id, 999999)


async def test_get_version_raises_when_version_belongs_to_a_different_article(
    article: Article, plan: Plan
) -> None:
    plan_id, item_id = await _create_plan_item(plan)
    article_id = await article.create(plan_id, item_id, "Topic A", "zen")
    other_id = await article.create(plan_id, item_id, "Topic A", "vc")
    await article.record_version(article_id, _VERSION)
    versions = await article.list_versions(article_id)

    with pytest.raises(ArticleVersionNotFound):
        await article.get_version(other_id, versions[0].id)


async def test_request_generation_creates_the_article_and_enqueues_generate_article(
    article: Article, plan: Plan, queue: JobQueue
) -> None:
    plan_id, item_id = await _create_plan_item(plan)

    article_id = await article.request_generation(
        plan_id, item_id, "Topic A", "summary text", ["kw1", "kw2"], "zen"
    )

    claimed = await queue.claim_next()
    assert claimed is not None
    assert claimed.job_type == "generate_article"
    assert claimed.payload == {
        "article_id": article_id,
        "title": "Topic A",
        "platform": "zen",
        "summary": "summary text",
        "keywords": ["kw1", "kw2"],
    }
    with pytest.raises(ArticleNotReady):
        await article.get(article_id)


async def test_request_generation_is_idempotent_for_the_same_plan_item_and_platform(
    article: Article, plan: Plan, queue: JobQueue
) -> None:
    plan_id, item_id = await _create_plan_item(plan)

    first_id = await article.request_generation(plan_id, item_id, "Topic A", "s", [], "zen")
    second_id = await article.request_generation(plan_id, item_id, "Topic A", "s", [], "zen")

    assert first_id == second_id
    first_claim = await queue.claim_next()
    assert first_claim is not None
    second_claim = await queue.claim_next()
    assert second_claim is None
