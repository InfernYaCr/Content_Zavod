import asyncpg
import pytest
from testcontainers.community.postgres import PostgresContainer

import content_zavod
from content_zavod import job_queue, telegram, yandex


def test_modules_importable() -> None:
    assert content_zavod is not None
    assert job_queue is not None
    assert yandex is not None
    assert telegram is not None


@pytest.mark.asyncio
async def test_postgres_container_is_reachable() -> None:
    with PostgresContainer("postgres:16-alpine") as postgres:
        conn = await asyncpg.connect(
            host=postgres.get_container_host_ip(),
            port=postgres.get_exposed_port(5432),
            user=postgres.username,
            password=postgres.password,
            database=postgres.dbname,
        )
        try:
            result = await conn.fetchval("SELECT 1")
            assert result == 1
        finally:
            await conn.close()
