from __future__ import annotations

import asyncio

import asyncpg

from content_zavod.job_queue import JobQueue, JobResult, run_notifications


async def test_claim_notification_returns_none_when_nothing_is_pending(queue: JobQueue) -> None:
    assert await queue.claim_notification() is None


async def test_claim_notification_returns_the_result_of_a_done_job(queue: JobQueue) -> None:
    job_id = await queue.enqueue("generate_plan", {}, idempotency_key="plan-1")
    job = await queue.claim_next()
    assert job is not None
    await queue.complete(job_id, {"plan": "ok"}, job.lease_token)

    claimed = await queue.claim_notification()

    assert claimed is not None
    assert claimed.result == JobResult(
        job_id=job_id, job_type="generate_plan", status="done", output={"plan": "ok"}, error=None
    )


async def test_two_concurrent_notification_claims_never_return_the_same_job(queue: JobQueue) -> None:
    job_id = await queue.enqueue("generate_plan", {}, idempotency_key="plan-1")
    job = await queue.claim_next()
    assert job is not None
    await queue.complete(job_id, {"plan": "ok"}, job.lease_token)

    first, second = await asyncio.gather(queue.claim_notification(), queue.claim_notification())

    claimed = [c for c in (first, second) if c is not None]
    assert len(claimed) == 1


async def test_run_notifications_calls_handle_exactly_once_for_a_successful_delivery(
    queue: JobQueue,
) -> None:
    job_id = await queue.enqueue("generate_plan", {}, idempotency_key="plan-1")
    job = await queue.claim_next()
    assert job is not None
    await queue.complete(job_id, {"plan": "ok"}, job.lease_token)

    received: list[JobResult] = []
    stop = asyncio.Event()

    async def handle(result: JobResult) -> None:
        received.append(result)
        stop.set()

    await asyncio.wait_for(
        run_notifications(queue, handle, poll_interval=0.01, stop=stop), timeout=5
    )

    assert len(received) == 1
    assert received[0].job_id == job_id

    # A second pass finds nothing left to notify - notified_at was committed.
    assert await queue.claim_notification() is None


async def test_run_notifications_retries_a_failing_handler_until_it_succeeds(
    pool: asyncpg.Pool,
) -> None:
    queue = JobQueue(pool, notification_max_attempts=5, notification_base_delay=0.01)
    await queue.ensure_schema()
    await pool.execute("TRUNCATE TABLE jobs RESTART IDENTITY")
    job_id = await queue.enqueue("generate_plan", {}, idempotency_key="plan-1")
    job = await queue.claim_next()
    assert job is not None
    await queue.complete(job_id, {"plan": "ok"}, job.lease_token)

    call_count = 0
    stop = asyncio.Event()

    async def flaky_handle(result: JobResult) -> None:
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise RuntimeError("transient failure")
        stop.set()

    await asyncio.wait_for(
        run_notifications(queue, flaky_handle, poll_interval=0.01, stop=stop), timeout=5
    )

    assert call_count == 3
    row = await pool.fetchrow("SELECT notified_at FROM jobs WHERE id = $1", job_id)
    assert row["notified_at"] is not None


async def test_run_notifications_gives_up_after_max_attempts_and_marks_delivered(
    pool: asyncpg.Pool,
) -> None:
    queue = JobQueue(pool, notification_max_attempts=2, notification_base_delay=0.01)
    await queue.ensure_schema()
    await pool.execute("TRUNCATE TABLE jobs RESTART IDENTITY")
    job_id = await queue.enqueue("generate_plan", {}, idempotency_key="plan-1")
    job = await queue.claim_next()
    assert job is not None
    await queue.complete(job_id, {"plan": "ok"}, job.lease_token)

    call_count = 0

    async def always_failing_handle(result: JobResult) -> None:
        nonlocal call_count
        call_count += 1
        raise RuntimeError("permanent failure")

    stop = asyncio.Event()

    async def watcher() -> None:
        row = None
        while row is None or row["notified_at"] is None:
            row = await pool.fetchrow("SELECT notified_at FROM jobs WHERE id = $1", job_id)
            await asyncio.sleep(0.01)
        stop.set()

    await asyncio.wait_for(
        asyncio.gather(
            run_notifications(queue, always_failing_handle, poll_interval=0.01, stop=stop),
            watcher(),
        ),
        timeout=5,
    )

    assert call_count == 2
    row = await pool.fetchrow("SELECT notified_at FROM jobs WHERE id = $1", job_id)
    assert row["notified_at"] is not None
