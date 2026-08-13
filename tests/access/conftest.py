from __future__ import annotations

from collections.abc import AsyncIterator

import asyncpg
import pytest_asyncio

from content_zavod.access import JoinRequests, Membership


@pytest_asyncio.fixture(loop_scope="session")
async def membership(pool: asyncpg.Pool) -> AsyncIterator[Membership]:
    instance = Membership(pool)
    await pool.execute("TRUNCATE TABLE members")
    yield instance


@pytest_asyncio.fixture(loop_scope="session")
async def join_requests(pool: asyncpg.Pool) -> AsyncIterator[JoinRequests]:
    instance = JoinRequests(pool)
    await pool.execute("TRUNCATE TABLE join_request_broadcasts, join_requests")
    yield instance
