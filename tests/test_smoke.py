import asyncpg
import pytest

import content_zavod
from content_zavod import job_queue, telegram, yandex


def test_modules_importable() -> None:
    assert content_zavod is not None
    assert job_queue is not None
    assert yandex is not None
    assert telegram is not None


@pytest.mark.asyncio
async def test_postgres_container_is_reachable(pool: asyncpg.Pool) -> None:
    result = await pool.fetchval("SELECT 1")
    assert result == 1
