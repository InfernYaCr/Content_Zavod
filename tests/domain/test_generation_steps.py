import pytest

from content_zavod.domain import Article, GenerationSteps, Plan, TopicDraft
from content_zavod.job_queue import JobQueue

_STEP = {
    "step_name": "outline",
    "provider": "yandex",
    "model": "yandexgpt/latest",
    "params": {"temperature": 0.7},
    "prompt_template_version": "outline-v1",
    "prompt_hash": "abc123",
    "tokens": 42,
    "usage_missing": False,
    "latency_ms": 120,
    "cost": 0.05,
}


async def _enqueue_job(queue: JobQueue, key: str) -> int:
    return await queue.enqueue("generate_article", {}, idempotency_key=key)


async def _create_article(plan: Plan, article: Article) -> str:
    plan_id = await plan.add_topics("Week 1", [TopicDraft(title="Topic A")])
    view = await plan.get(plan_id)
    item_id = view.items[0].id
    return await article.create(plan_id, item_id, "Topic A", "zen")


async def test_record_many_persists_every_step(
    queue: JobQueue, generation_steps: GenerationSteps
) -> None:
    job_id = await _enqueue_job(queue, "job-1")

    await generation_steps.record_many(
        job_id=job_id,
        job_type="generate_article",
        article_id=None,
        steps=[_STEP, {**_STEP, "step_name": "draft", "cost": 0.1}],
    )

    steps = await generation_steps.list_for_job(job_id)
    assert [s.step_name for s in steps] == ["outline", "draft"]
    assert steps[0].cost == 0.05
    assert steps[0].prompt_template_version == "outline-v1"
    assert steps[0].usage_missing is False


async def test_record_many_is_a_no_op_for_an_empty_list(
    queue: JobQueue, generation_steps: GenerationSteps
) -> None:
    job_id = await _enqueue_job(queue, "job-1")

    await generation_steps.record_many(
        job_id=job_id, job_type="generate_article", article_id=None, steps=[]
    )

    assert await generation_steps.list_for_job(job_id) == []


async def test_record_many_links_steps_to_an_article(
    queue: JobQueue, plan: Plan, article: Article, generation_steps: GenerationSteps
) -> None:
    article_id = await _create_article(plan, article)
    job_id = await _enqueue_job(queue, "job-1")

    await generation_steps.record_many(
        job_id=job_id, job_type="generate_article", article_id=article_id, steps=[_STEP]
    )

    steps = await generation_steps.list_for_article(article_id)
    assert len(steps) == 1
    assert steps[0].article_id == article_id


async def test_job_cost_sums_every_recorded_step(
    queue: JobQueue, generation_steps: GenerationSteps
) -> None:
    job_id = await _enqueue_job(queue, "job-1")

    await generation_steps.record_many(
        job_id=job_id,
        job_type="generate_article",
        article_id=None,
        steps=[{**_STEP, "cost": 0.05}, {**_STEP, "step_name": "draft", "cost": 0.1}],
    )

    summary = await generation_steps.job_cost(job_id)
    assert summary.total == pytest.approx(0.15)
    assert summary.complete is True
    assert summary.step_count == 2


async def test_job_cost_accumulates_across_multiple_attempts(
    queue: JobQueue, generation_steps: GenerationSteps
) -> None:
    job_id = await _enqueue_job(queue, "job-1")

    # A failed first attempt still spent money on the steps it completed - and a
    # retried second attempt runs the whole pipeline again from scratch (#74).
    await generation_steps.record_many(
        job_id=job_id, job_type="generate_article", article_id=None, steps=[{**_STEP, "cost": 0.05}]
    )
    await generation_steps.record_many(
        job_id=job_id,
        job_type="generate_article",
        article_id=None,
        steps=[{**_STEP, "cost": 0.05}, {**_STEP, "step_name": "draft", "cost": 0.1}],
    )

    summary = await generation_steps.job_cost(job_id)
    assert summary.total == pytest.approx(0.20)
    assert summary.step_count == 3


async def test_job_cost_is_marked_incomplete_when_any_step_cost_is_unknown(
    queue: JobQueue, generation_steps: GenerationSteps
) -> None:
    job_id = await _enqueue_job(queue, "job-1")

    await generation_steps.record_many(
        job_id=job_id,
        job_type="generate_article",
        article_id=None,
        steps=[{**_STEP, "cost": 0.05}, {**_STEP, "step_name": "draft", "cost": None}],
    )

    summary = await generation_steps.job_cost(job_id)
    assert summary.total == 0.05
    assert summary.complete is False
