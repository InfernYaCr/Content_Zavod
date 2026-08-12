from __future__ import annotations

from collections.abc import AsyncIterator

import asyncpg
import pytest
import pytest_asyncio
from testcontainers.community.postgres import PostgresContainer

from content_zavod.scheduling import ScheduleSettings


@pytest.fixture(scope="session")
def postgres_container() -> AsyncIterator[PostgresContainer]:
    with PostgresContainer("postgres:16-alpine") as container:
        yield container


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def pool(postgres_container: PostgresContainer) -> AsyncIterator[asyncpg.Pool]:
    db_pool = await asyncpg.create_pool(
        host=postgres_container.get_container_host_ip(),
        port=postgres_container.get_exposed_port(5432),
        user=postgres_container.username,
        password=postgres_container.password,
        database=postgres_container.dbname,
    )
    assert db_pool is not None
    try:
        yield db_pool
    finally:
        await db_pool.close()


@pytest_asyncio.fixture(loop_scope="session")
async def schedule_settings(pool: asyncpg.Pool) -> AsyncIterator[ScheduleSettings]:
    instance = ScheduleSettings(pool)
    await instance.ensure_schema()
    await pool.execute("TRUNCATE TABLE schedule_settings")
    yield instance
