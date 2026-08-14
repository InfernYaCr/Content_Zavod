from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta

import asyncpg
import pytest

from content_zavod.domain import GenerationSteps
from content_zavod.job_queue import ClaimedJob, JobPartialFailure, JobQueue, run_worker


async def test_claim_next_returns_none_when_queue_is_empty(queue: JobQueue) -> None:
    assert await queue.claim_next() is None


async def test_claim_next_marks_the_job_running_and_returns_its_payload(queue: JobQueue) -> None:
    job_id = await queue.enqueue("generate_plan", {"week": 1}, idempotency_key="plan-1")

    claimed = await queue.claim_next()

    assert claimed is not None
    assert claimed.id == job_id
    assert claimed.job_type == "generate_plan"
    assert claimed.payload == {"week": 1}
    assert claimed.attempts == 1
    assert await queue.get_status(job_id) == "running"


async def test_two_concurrent_claims_never_return_the_same_job(queue: JobQueue) -> None:
    await queue.enqueue("generate_plan", {"week": 1}, idempotency_key="plan-1")

    first, second = await asyncio.gather(queue.claim_next(), queue.claim_next())

    claimed = [c for c in (first, second) if c is not None]
    assert len(claimed) == 1


async def test_complete_writes_result_and_status_together(queue: JobQueue) -> None:
    job_id = await queue.enqueue("generate_plan", {"week": 1}, idempotency_key="plan-1")
    claimed = await queue.claim_next()
    assert claimed is not None

    assert await queue.complete(job_id, {"plan": "done"}, claimed.lease_token)

    assert await queue.get_status(job_id) == "done"


async def test_fail_requeues_with_backoff_until_attempts_are_exhausted(pool: asyncpg.Pool) -> None:
    queue = JobQueue(pool, max_attempts=2, base_delay=100.0)
    await pool.execute("TRUNCATE TABLE jobs, generation_steps RESTART IDENTITY")
    job_id = await queue.enqueue("generate_plan", {}, idempotency_key="plan-1")

    first = await queue.claim_next()
    assert first is not None
    assert await queue.fail(job_id, "boom", first.lease_token)
    status_after_first_failure = await queue.get_status(job_id)
    row = await pool.fetchrow("SELECT run_at FROM jobs WHERE id = $1", job_id)

    assert status_after_first_failure == "queued"
    assert row["run_at"] > datetime.now(UTC)

    await pool.execute("UPDATE jobs SET run_at = now() WHERE id = $1", job_id)
    second = await queue.claim_next()
    assert second is not None
    assert await queue.fail(job_id, "boom again", second.lease_token)

    assert await queue.get_status(job_id) == "failed"


async def test_recover_stuck_requeues_abandoned_running_jobs(pool: asyncpg.Pool) -> None:
    queue = JobQueue(pool, stuck_timeout=timedelta(seconds=0))
    await pool.execute("TRUNCATE TABLE jobs, generation_steps RESTART IDENTITY")
    job_id = await queue.enqueue("generate_plan", {}, idempotency_key="plan-1")
    await queue.claim_next()

    recovered = await queue.recover_stuck()

    assert recovered == 1
    assert await queue.get_status(job_id) == "queued"


async def test_stale_worker_cannot_complete_or_fail_a_reclaimed_job(pool: asyncpg.Pool) -> None:
    queue = JobQueue(pool, stuck_timeout=timedelta(seconds=0))
    await pool.execute("TRUNCATE TABLE jobs, generation_steps RESTART IDENTITY")
    job_id = await queue.enqueue("generate_plan", {}, idempotency_key="plan-1")
    stale = await queue.claim_next()
    assert stale is not None

    assert await queue.recover_stuck() == 1
    current = await queue.claim_next()
    assert current is not None
    assert current.lease_token != stale.lease_token

    assert not await queue.complete(job_id, {"stale": True}, stale.lease_token)
    assert not await queue.fail(job_id, "stale", stale.lease_token)
    assert await queue.complete(job_id, {"current": True}, current.lease_token)

    row = await pool.fetchrow("SELECT status, output FROM jobs WHERE id = $1", job_id)
    assert row["status"] == "done"
    assert json.loads(row["output"]) == {"current": True}


async def test_heartbeat_prevents_recovery_until_lease_becomes_stale(pool: asyncpg.Pool) -> None:
    queue = JobQueue(pool, stuck_timeout=timedelta(seconds=10))
    await pool.execute("TRUNCATE TABLE jobs, generation_steps RESTART IDENTITY")
    job_id = await queue.enqueue("generate_plan", {}, idempotency_key="plan-1")
    claimed = await queue.claim_next()
    assert claimed is not None

    await pool.execute(
        "UPDATE jobs SET locked_at = now() - interval '9 seconds' WHERE id = $1", job_id
    )
    assert await queue.heartbeat(job_id, claimed.lease_token)
    assert await queue.recover_stuck() == 0
    assert await queue.get_status(job_id) == "running"

    await pool.execute(
        "UPDATE jobs SET locked_at = now() - interval '11 seconds' WHERE id = $1", job_id
    )
    assert await queue.recover_stuck() == 1
    assert await queue.get_status(job_id) == "queued"


async def test_worker_heartbeats_while_handler_is_running(queue: JobQueue) -> None:
    await queue.enqueue("generate_plan", {}, idempotency_key="plan-1")
    heartbeat_seen = asyncio.Event()
    finish_handler = asyncio.Event()
    stop = asyncio.Event()
    original_heartbeat = queue.heartbeat

    async def recording_heartbeat(job_id: int, lease_token: str) -> bool:
        renewed = await original_heartbeat(job_id, lease_token)
        heartbeat_seen.set()
        finish_handler.set()
        return renewed

    queue.heartbeat = recording_heartbeat  # type: ignore[method-assign]

    async def handler(payload: dict) -> dict:
        await finish_handler.wait()
        stop.set()
        return {}

    await asyncio.wait_for(
        run_worker(
            queue,
            {"generate_plan": handler},
            heartbeat_interval=0.001,
            poll_interval=0.001,
            stop=stop,
        ),
        timeout=5,
    )

    assert heartbeat_seen.is_set()


async def test_run_worker_recovers_stuck_jobs_periodically_even_when_continuously_busy(
    queue: JobQueue,
) -> None:
    for i in range(5):
        await queue.enqueue("generate_plan", {"i": i}, idempotency_key=f"plan-{i}")

    recovery_calls = 0
    original_recover_stuck = queue.recover_stuck

    async def counting_recover_stuck() -> int:
        nonlocal recovery_calls
        recovery_calls += 1
        return await original_recover_stuck()

    queue.recover_stuck = counting_recover_stuck  # type: ignore[method-assign]

    processed = 0
    stop = asyncio.Event()

    async def handler(payload: dict) -> dict:
        nonlocal processed
        processed += 1
        if processed >= 5:
            stop.set()
        return {}

    fake_time = 0.0

    def clock() -> float:
        nonlocal fake_time
        fake_time += 0.02
        return fake_time

    await asyncio.wait_for(
        run_worker(
            queue,
            {"generate_plan": handler},
            poll_interval=0.001,
            stuck_recovery_interval=0.05,
            clock=clock,
            stop=stop,
        ),
        timeout=5,
    )

    # Never idle (5 jobs always available) - a recovery pass must still fire
    # more than once as time crosses stuck_recovery_interval repeatedly.
    assert recovery_calls >= 2


async def test_run_worker_executes_the_matching_handler(queue: JobQueue) -> None:
    job_id = await queue.enqueue("generate_plan", {"week": 1}, idempotency_key="plan-1")

    async def handler(payload: dict) -> dict:
        return {"weeks_done": payload["week"]}

    stop = asyncio.Event()

    async def handle_and_stop(payload: dict) -> dict:
        result = await handler(payload)
        stop.set()
        return result

    await asyncio.wait_for(
        run_worker(queue, {"generate_plan": handle_and_stop}, poll_interval=0.01, stop=stop),
        timeout=5,
    )

    assert await queue.get_status(job_id) == "done"


async def test_run_worker_retries_a_failing_handler_and_eventually_marks_it_failed(
    pool: asyncpg.Pool,
) -> None:
    queue = JobQueue(pool, max_attempts=2, base_delay=0.01)
    await pool.execute("TRUNCATE TABLE jobs, generation_steps RESTART IDENTITY")
    job_id = await queue.enqueue("generate_plan", {}, idempotency_key="plan-1")

    call_count = 0

    async def failing_handler(payload: dict) -> dict:
        nonlocal call_count
        call_count += 1
        raise RuntimeError("boom")

    stop = asyncio.Event()

    async def watcher() -> None:
        # The worker exposes state through PostgreSQL, not an in-process Event;
        # bounded polling is the contract this integration test must observe.
        while await queue.get_status(job_id) != "failed":  # noqa: ASYNC110
            await asyncio.sleep(0.01)
        stop.set()

    await asyncio.wait_for(
        asyncio.gather(
            run_worker(queue, {"generate_plan": failing_handler}, poll_interval=0.01, stop=stop),
            watcher(),
        ),
        timeout=5,
    )

    assert call_count == 2
    assert await queue.get_status(job_id) == "failed"


async def test_run_worker_calls_on_attempt_with_output_on_success(queue: JobQueue) -> None:
    await queue.enqueue("generate_plan", {"week": 1}, idempotency_key="plan-1")
    seen: list[tuple[ClaimedJob, dict | None, BaseException | None]] = []
    stop = asyncio.Event()

    async def handler(payload: dict) -> dict:
        return {"steps": [{"step_name": "outline"}]}

    async def on_attempt(claimed: ClaimedJob, output: dict | None, error) -> None:
        seen.append((claimed, output, error))
        stop.set()

    await asyncio.wait_for(
        run_worker(
            queue,
            {"generate_plan": handler},
            poll_interval=0.01,
            stop=stop,
            on_attempt=on_attempt,
        ),
        timeout=5,
    )

    assert len(seen) == 1
    claimed, output, error = seen[0]
    assert claimed.job_type == "generate_plan"
    assert output == {"steps": [{"step_name": "outline"}]}
    assert error is None


async def test_run_worker_calls_on_attempt_with_error_on_failure(pool: asyncpg.Pool) -> None:
    queue = JobQueue(pool, max_attempts=1, base_delay=0.01)
    await pool.execute("TRUNCATE TABLE jobs, generation_steps RESTART IDENTITY")
    await queue.enqueue("generate_plan", {}, idempotency_key="plan-1")
    seen: list[BaseException | None] = []
    stop = asyncio.Event()

    async def failing_handler(payload: dict) -> dict:
        raise JobPartialFailure("boom", {"steps": [{"step_name": "outline"}]})

    async def on_attempt(claimed: ClaimedJob, output: dict | None, error) -> None:
        seen.append(error)
        stop.set()

    await asyncio.wait_for(
        run_worker(
            queue,
            {"generate_plan": failing_handler},
            poll_interval=0.01,
            stop=stop,
            on_attempt=on_attempt,
        ),
        timeout=5,
    )

    assert len(seen) == 1
    assert isinstance(seen[0], JobPartialFailure)
    assert seen[0].partial_output == {"steps": [{"step_name": "outline"}]}


async def test_job_cost_sums_every_attempt_end_to_end_through_a_retry(pool: asyncpg.Pool) -> None:
    """Wires `run_worker`'s `on_attempt` hook to a real `GenerationSteps` (as
    `entrypoints.worker._make_on_attempt` does) to prove a Job's cost reflects every
    attempt including a failed one, not only the retry that finally succeeds (#74)."""
    queue = JobQueue(pool, max_attempts=2, base_delay=0.01)
    await pool.execute("TRUNCATE TABLE jobs, generation_steps RESTART IDENTITY")
    generation_steps = GenerationSteps(pool)
    job_id = await queue.enqueue("generate_article", {}, idempotency_key="article-1")

    def _step(step_name: str, cost: float) -> dict:
        return {
            "step_name": step_name,
            "provider": "yandex",
            "model": "yandexgpt/latest",
            "params": {},
            "prompt_template_version": "v1",
            "prompt_hash": "abc",
            "tokens": 10,
            "usage_missing": False,
            "latency_ms": 5,
            "cost": cost,
        }

    call_count = 0

    async def handler(payload: dict) -> dict:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise JobPartialFailure("model unavailable", {"steps": [_step("outline", 0.05)]})
        return {"steps": [_step("outline", 0.05), _step("draft", 0.1)]}

    async def on_attempt(claimed, output, error) -> None:
        if output is not None:
            steps = output.get("steps") or []
        elif isinstance(error, JobPartialFailure):
            steps = error.partial_output.get("steps") or []
        else:
            steps = []
        await generation_steps.record_many(
            job_id=claimed.id, job_type=claimed.job_type, article_id=None, steps=steps
        )

    stop = asyncio.Event()

    async def watcher() -> None:
        while await queue.get_status(job_id) != "done":  # noqa: ASYNC110
            await asyncio.sleep(0.01)
        stop.set()

    await asyncio.wait_for(
        asyncio.gather(
            run_worker(
                queue,
                {"generate_article": handler},
                poll_interval=0.01,
                stop=stop,
                on_attempt=on_attempt,
            ),
            watcher(),
        ),
        timeout=5,
    )

    assert call_count == 2
    summary = await generation_steps.job_cost(job_id)
    assert summary.total == pytest.approx(0.05 + 0.05 + 0.1)
    assert summary.step_count == 3
