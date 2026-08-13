from __future__ import annotations

from collections.abc import AsyncIterator

import asyncpg
import pytest_asyncio

from content_zavod.owner_settings import OwnerSettingsStore


@pytest_asyncio.fixture(loop_scope="session")
async def owner_settings(pool: asyncpg.Pool) -> AsyncIterator[OwnerSettingsStore]:
    instance = OwnerSettingsStore(pool)
    await pool.execute("TRUNCATE TABLE owner_settings")
    yield instance
