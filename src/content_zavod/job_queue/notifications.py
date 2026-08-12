"""run_notifications: delivers exactly one notification per finished job.

`notified_at` is only set after `handle` returns successfully. A failing
`handle` is retried with backoff; once notification attempts are exhausted
the job is marked delivered anyway (so one broken notification can't stall
the queue forever) and the failure is logged.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from .models import JobResult
from .queue import JobQueue

logger = logging.getLogger(__name__)


async def run_notifications(
    queue: JobQueue,
    handle: Callable[[JobResult], Awaitable[None]],
    *,
    poll_interval: float = 1.0,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    stop: asyncio.Event | None = None,
) -> None:
    while stop is None or not stop.is_set():
        claimed = await queue.claim_notification()
        if claimed is None:
            await sleep(poll_interval)
            continue

        try:
            await handle(claimed.result)
        except Exception as exc:
            if queue.notification_attempts_exhausted(claimed.notification_attempts):
                logger.error(
                    "Notification for job %s (%s) failed permanently after %d attempts: %s",
                    claimed.result.job_id,
                    claimed.result.job_type,
                    claimed.notification_attempts,
                    exc,
                )
                await queue.mark_notified(claimed.result.job_id)
            else:
                logger.warning(
                    "Notification for job %s (%s) failed, will retry: %s",
                    claimed.result.job_id,
                    claimed.result.job_type,
                    exc,
                )
                await queue.reschedule_notification(
                    claimed.result.job_id, claimed.notification_attempts
                )
        else:
            await queue.mark_notified(claimed.result.job_id)
