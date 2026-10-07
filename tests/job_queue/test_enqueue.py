import pytest

from content_zavod.job_queue import JobNotFound, JobQueue


async def test_enqueue_returns_a_job_id_and_the_job_is_queued(queue: JobQueue) -> None:
    job_id = await queue.enqueue("generate_plan", {"week": 1}, idempotency_key="plan-1")

    assert await queue.get_status(job_id) == "queued"


async def test_enqueue_with_same_idempotency_key_does_not_create_a_second_job(
    queue: JobQueue,
) -> None:
    first_id = await queue.enqueue("generate_plan", {"week": 1}, idempotency_key="plan-1")
    second_id = await queue.enqueue("generate_plan", {"week": 1}, idempotency_key="plan-1")

    assert first_id == second_id


async def test_get_status_raises_for_unknown_job(queue: JobQueue) -> None:
    with pytest.raises(JobNotFound):
        await queue.get_status(999_999)


async def test_get_job_returns_payload_and_output_once_done(queue: JobQueue) -> None:
    job_id = await queue.enqueue("suggest_directions", {"chat_id": 1}, idempotency_key="s-1")

    queued = await queue.get_job(job_id)
    assert queued is not None
    assert (queued.job_type, queued.status, queued.payload, queued.output) == (
        "suggest_directions",
        "queued",
        {"chat_id": 1},
        None,
    )

    claimed = await queue.claim_next()
    assert claimed is not None
    await queue.complete(job_id, {"queries": ["хлеб"]}, claimed.lease_token)

    done = await queue.get_job(job_id)
    assert done is not None and done.status == "done" and done.output == {"queries": ["хлеб"]}


async def test_get_job_is_none_for_unknown_job(queue: JobQueue) -> None:
    assert await queue.get_job(999_999) is None
