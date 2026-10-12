from __future__ import annotations

from collections.abc import AsyncIterator

import asyncpg
import pytest
import pytest_asyncio
from testcontainers.community.postgres import PostgresContainer

from content_zavod.migrations import run_migrations
from content_zavod.workspaces import WorkspaceId

from .tenancy import WS_A, WS_B


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
        await run_migrations(db_pool)
        await db_pool.execute(
            "INSERT INTO workspaces (id, owner_telegram_id, timezone) "
            "VALUES ($1, $1, 'Europe/Moscow'), ($2, $2, 'Europe/Moscow')",
            WS_A,
            WS_B,
        )
        yield db_pool
    finally:
        await db_pool.close()


@pytest.fixture
def two_workspaces(pool: asyncpg.Pool) -> tuple[WorkspaceId, WorkspaceId]:
    """Workspaces A and B for a cross-tenant test: the same data in both, then an action as A
    on B's id must find nothing and change nothing in B."""
    return WS_A, WS_B
