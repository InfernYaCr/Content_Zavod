from __future__ import annotations

from collections.abc import AsyncIterator

import asyncpg
import pytest_asyncio

from content_zavod.owner_settings import WorkspaceSettingsStore


@pytest_asyncio.fixture(loop_scope="session")
async def owner_settings(pool: asyncpg.Pool) -> AsyncIterator[WorkspaceSettingsStore]:
    instance = WorkspaceSettingsStore(pool)
    await pool.execute("TRUNCATE TABLE workspace_settings, bot_state")
    yield instance
