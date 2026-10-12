"""`claim_next` serves Пространства in turn, at most one running Job each (ADR-0015)."""

from __future__ import annotations

import asyncpg
import pytest_asyncio

from content_zavod.job_queue import JobId, JobQueue
from content_zavod.workspaces import WorkspaceId
from tests.tenancy import WS_A, WS_B


@pytest_asyncio.fixture(autouse=True, loop_scope="session")
async def _no_workspace_served_yet(pool: asyncpg.Pool) -> None:
    await pool.execute("UPDATE workspaces SET last_job_claimed_at = NULL")


async def _enqueue(queue: JobQueue, workspace_id: WorkspaceId, count: int) -> list[JobId]:
    return [
        await queue.enqueue(
            workspace_id, "generate_article", {"n": n}, idempotency_key=f"{workspace_id}-{n}"
        )
        for n in range(count)
    ]


async def _claim_and_finish(queue: JobQueue) -> JobId:
    claimed = await queue.claim_next()
    assert claimed is not None
    assert await queue.complete(claimed.id, {}, claimed.lease_token)
    return claimed.id


async def test_claim_next_takes_no_second_job_of_a_workspace_with_one_running(
    queue: JobQueue,
) -> None:
    first_a, second_a = await _enqueue(queue, WS_A, 2)

    running = await queue.claim_next()

    assert running is not None and running.id == first_a
    assert await queue.claim_next() is None
    assert await queue.complete(running.id, {}, running.lease_token)
    following = await queue.claim_next()
    assert following is not None and following.id == second_a


async def test_workspaces_are_served_in_turn_not_in_queue_order(queue: JobQueue) -> None:
    """A1..A6 queued before B1: B1 is the second Job taken, even with A1 already finished -
    six Статьи of A don't make B wait until all six are written."""
    a_jobs = await _enqueue(queue, WS_A, 6)
    (b1,) = await _enqueue(queue, WS_B, 1)

    taken = [await _claim_and_finish(queue) for _ in range(3)]

    assert taken == [a_jobs[0], b1, a_jobs[1]]
