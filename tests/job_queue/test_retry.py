import asyncpg
import pytest

from content_zavod.job_queue import JobNotFound, JobQueue


async def test_retry_resets_a_failed_job_to_queued(pool: asyncpg.Pool) -> None:
    queue = JobQueue(pool, max_attempts=1)
    await queue.ensure_schema()
    await pool.execute("TRUNCATE TABLE jobs RESTART IDENTITY")
    job_id = await queue.enqueue("generate_plan", {"week": 1}, idempotency_key="plan-1")
    claimed = await queue.claim_next()
    assert claimed is not None
    await queue.fail(job_id, "boom", claimed.lease_token)

    assert await queue.retry(job_id)

    assert await queue.get_status(job_id) == "queued"


async def test_retry_lets_the_job_be_claimed_again(pool: asyncpg.Pool) -> None:
    queue = JobQueue(pool, max_attempts=1)
    await queue.ensure_schema()
    await pool.execute("TRUNCATE TABLE jobs RESTART IDENTITY")
    job_id = await queue.enqueue("generate_plan", {"week": 1}, idempotency_key="plan-1")
    first_claim = await queue.claim_next()
    assert first_claim is not None
    await queue.fail(job_id, "boom", first_claim.lease_token)

    assert await queue.retry(job_id)
    second_claim = await queue.claim_next()

    assert second_claim is not None
    assert second_claim.id == job_id
    assert second_claim.payload == {"week": 1}


async def test_retry_on_unknown_job_raises_job_not_found(queue: JobQueue) -> None:
    with pytest.raises(JobNotFound):
        await queue.retry(999_999)


async def test_retry_is_an_explicit_noop_for_non_failed_job(queue: JobQueue) -> None:
    job_id = await queue.enqueue("generate_plan", {}, idempotency_key="plan-1")

    assert not await queue.retry(job_id)
    assert await queue.get_status(job_id) == "queued"
