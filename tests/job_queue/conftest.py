from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import timedelta

import asyncpg
import pytest_asyncio

from content_zavod.job_queue import JobQueue


@pytest_asyncio.fixture(loop_scope="session")
async def queue(pool: asyncpg.Pool) -> AsyncIterator[JobQueue]:
    job_queue = JobQueue(
        pool,
        base_delay=0.01,
        notification_base_delay=0.01,
        stuck_timeout=timedelta(seconds=0),
    )
    await pool.execute("TRUNCATE TABLE jobs RESTART IDENTITY")
    yield job_queue
