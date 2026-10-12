from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import asyncpg


@asynccontextmanager
async def transaction(
    pool: asyncpg.Pool, conn: asyncpg.Connection | None
) -> AsyncIterator[asyncpg.Connection]:
    """Inside the caller's transaction when it passes `conn` (the one `Limits.reserve` checked,
    ADR-0015) - as a savepoint; otherwise in a transaction of its own."""
    if conn is not None:
        async with conn.transaction():
            yield conn
        return
    async with pool.acquire() as acquired, acquired.transaction():
        yield acquired
