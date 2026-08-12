"""run_worker: pulls queued jobs and runs them against registered handlers."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable

from .models import JobHandler
from .queue import JobQueue

logger = logging.getLogger(__name__)


async def run_worker(
    queue: JobQueue,
    handlers: dict[str, JobHandler],
    *,
    poll_interval: float = 1.0,
    stuck_recovery_interval: float = 60.0,
    heartbeat_interval: float | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    stop: asyncio.Event | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> None:
    last_recovery = clock() - stuck_recovery_interval
    while stop is None or not stop.is_set():
        if clock() - last_recovery >= stuck_recovery_interval:
            await queue.recover_stuck()
            last_recovery = clock()

        claimed = await queue.claim_next()
        if claimed is None:
            await sleep(poll_interval)
            continue

        handler = handlers.get(claimed.job_type)
        if handler is None:
            await queue.fail(
                claimed.id,
                f"No handler registered for job_type={claimed.job_type!r}",
                claimed.lease_token,
            )
            continue

        handler_task = asyncio.create_task(handler(claimed.payload))
        heartbeat_task = asyncio.create_task(
            _heartbeat_while_running(
                queue,
                claimed.id,
                claimed.lease_token,
                handler_task,
                interval=heartbeat_interval or queue.heartbeat_interval,
                sleep=sleep,
            )
        )
        try:
            output = await handler_task
        except Exception as exc:
            logger.warning("Job %s (%s) failed: %s", claimed.id, claimed.job_type, exc)
            await queue.fail(claimed.id, str(exc), claimed.lease_token)
        else:
            await queue.complete(claimed.id, output, claimed.lease_token)
        finally:
            heartbeat_task.cancel()
            await asyncio.gather(heartbeat_task, return_exceptions=True)


async def _heartbeat_while_running(
    queue: JobQueue,
    job_id: int,
    lease_token: str,
    handler_task: asyncio.Task[dict],
    *,
    interval: float,
    sleep: Callable[[float], Awaitable[None]],
) -> None:
    while not handler_task.done():
        await sleep(interval)
        if handler_task.done() or not await queue.heartbeat(job_id, lease_token):
            return
