import pytest

from content_zavod.job_queue import JobNotFound, JobQueue


async def test_retry_resets_a_failed_job_to_queued(queue: JobQueue) -> None:
    job_id = await queue.enqueue("generate_plan", {"week": 1}, idempotency_key="plan-1")
    claimed = await queue.claim_next()
    assert claimed is not None
    await queue.fail(job_id, "boom")

    await queue.retry(job_id)

    assert await queue.get_status(job_id) == "queued"


async def test_retry_lets_the_job_be_claimed_again(queue: JobQueue) -> None:
    job_id = await queue.enqueue("generate_plan", {"week": 1}, idempotency_key="plan-1")
    first_claim = await queue.claim_next()
    assert first_claim is not None
    await queue.fail(job_id, "boom")

    await queue.retry(job_id)
    second_claim = await queue.claim_next()

    assert second_claim is not None
    assert second_claim.id == job_id
    assert second_claim.payload == {"week": 1}


async def test_retry_on_unknown_job_raises_job_not_found(queue: JobQueue) -> None:
    with pytest.raises(JobNotFound):
        await queue.retry(999_999)
