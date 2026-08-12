from __future__ import annotations

from collections.abc import AsyncIterator

import asyncpg
import pytest_asyncio

from content_zavod.scheduling import ScheduleSettings


@pytest_asyncio.fixture(loop_scope="session")
async def schedule_settings(pool: asyncpg.Pool) -> AsyncIterator[ScheduleSettings]:
    instance = ScheduleSettings(pool)
    await instance.ensure_schema()
    await pool.execute("TRUNCATE TABLE schedule_settings")
    yield instance
