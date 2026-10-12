import pytest

from content_zavod.job_queue import JobNotFound, JobQueue
from tests.tenancy import WS_A, WS_B


async def test_enqueue_returns_a_job_id_and_the_job_is_queued(queue: JobQueue) -> None:
    job_id = await queue.enqueue(WS_A, "generate_plan", {"week": 1}, idempotency_key="plan-1")

    assert await queue.get_status(WS_A, job_id) == "queued"


async def test_enqueue_with_same_idempotency_key_does_not_create_a_second_job(
    queue: JobQueue,
) -> None:
    first_id = await queue.enqueue(WS_A, "generate_plan", {"week": 1}, idempotency_key="plan-1")
    second_id = await queue.enqueue(WS_A, "generate_plan", {"week": 1}, idempotency_key="plan-1")

    assert first_id == second_id


async def test_get_status_raises_for_unknown_job(queue: JobQueue) -> None:
    with pytest.raises(JobNotFound):
        await queue.get_status(WS_A, 999_999)


async def test_get_job_returns_payload_and_output_once_done(queue: JobQueue) -> None:
    job_id = await queue.enqueue(WS_A, "suggest_directions", {"chat_id": 1}, idempotency_key="s-1")

    queued = await queue.get_job(WS_A, job_id)
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

    done = await queue.get_job(WS_A, job_id)
    assert done is not None and done.status == "done" and done.output == {"queries": ["хлеб"]}


async def test_get_job_is_none_for_unknown_job(queue: JobQueue) -> None:
    assert await queue.get_job(WS_A, 999_999) is None


async def _payload(queue: JobQueue, job_id: int) -> dict:
    job = await queue.get_job(WS_A, job_id)
    assert job is not None
    return job.payload


async def test_drop_payload_keys_strips_a_finished_jobs_input_in_its_own_workspace_only(
    queue: JobQueue,
) -> None:
    """#134: the Образцы leave a finished `analyze_style` Job; an unfinished one keeps them."""
    payload = {"samples": ["текст"], "links": ["https://t.me/x"], "message_id": 7}
    job_id = await queue.enqueue(WS_A, "analyze_style", payload, idempotency_key="style-1")

    await queue.drop_payload_keys(WS_A, job_id, ["samples", "links"])
    claimed = await queue.claim_next()
    assert claimed is not None and claimed.payload == payload
    assert await queue.complete(job_id, {"status": "ok"}, claimed.lease_token)

    await queue.drop_payload_keys(WS_B, job_id, ["samples", "links"])
    assert await _payload(queue, job_id) == payload

    await queue.drop_payload_keys(WS_A, job_id, ["samples", "links"])
    assert await _payload(queue, job_id) == {"message_id": 7}
