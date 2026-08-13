from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import asyncpg
import pytest_asyncio
from testcontainers.community.postgres import PostgresContainer


@pytest_asyncio.fixture(loop_scope="session")
async def isolated_pool(postgres_container: PostgresContainer) -> AsyncIterator[asyncpg.Pool]:
    """A pool scoped to its own throwaway Postgres schema.

    Migration tests need to control exactly which migrations have run
    against a database (an "existing installation on the old baseline",
    a fresh install, ...), which the shared session `pool` fixture -
    already fully migrated and reused by every other test module - can't
    give them. A dedicated schema on the same container gets that
    isolation without paying for a second container per test.
    """
    connection_kwargs = {
        "host": postgres_container.get_container_host_ip(),
        "port": postgres_container.get_exposed_port(5432),
        "user": postgres_container.username,
        "password": postgres_container.password,
        "database": postgres_container.dbname,
    }
    schema_name = f"test_migrations_{uuid.uuid4().hex}"
    bootstrap_conn = await asyncpg.connect(**connection_kwargs)
    try:
        await bootstrap_conn.execute(f'CREATE SCHEMA "{schema_name}"')
    finally:
        await bootstrap_conn.close()

    schema_pool = await asyncpg.create_pool(
        **connection_kwargs, server_settings={"search_path": schema_name}
    )
    assert schema_pool is not None
    try:
        yield schema_pool
    finally:
        await schema_pool.close()
        cleanup_conn = await asyncpg.connect(**connection_kwargs)
        try:
            await cleanup_conn.execute(f'DROP SCHEMA "{schema_name}" CASCADE')
        finally:
            await cleanup_conn.close()
