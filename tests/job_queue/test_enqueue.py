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
