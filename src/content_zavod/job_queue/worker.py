"""run_worker: pulls queued jobs and runs them against registered handlers."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Awaitable, Callable

from .models import JobHandler
from .queue import JobQueue

logger = logging.getLogger(__name__)


async def run_worker(
    queue: JobQueue,
    handlers: dict[str, JobHandler],
    *,
    poll_interval: float = 1.0,
    stuck_recovery_interval: float = 60.0,
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
            await queue.fail(claimed.id, f"No handler registered for job_type={claimed.job_type!r}")
            continue

        try:
            output = await handler(claimed.payload)
        except Exception as exc:  # noqa: BLE001 - a failing handler must not crash the worker loop
            logger.warning("Job %s (%s) failed: %s", claimed.id, claimed.job_type, exc)
            await queue.fail(claimed.id, str(exc))
        else:
            await queue.complete(claimed.id, output)
