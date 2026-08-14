from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import timedelta

import asyncpg
import pytest_asyncio

from content_zavod.domain import Article, GenerationSteps, Plan
from content_zavod.job_queue import JobQueue


@pytest_asyncio.fixture(loop_scope="session")
async def queue(pool: asyncpg.Pool) -> AsyncIterator[JobQueue]:
    job_queue = JobQueue(
        pool,
        base_delay=0.01,
        notification_base_delay=0.01,
        stuck_timeout=timedelta(seconds=0),
    )
    yield job_queue


@pytest_asyncio.fixture(loop_scope="session")
async def plan(pool: asyncpg.Pool, queue: JobQueue) -> AsyncIterator[Plan]:
    yield Plan(pool, queue)


@pytest_asyncio.fixture(loop_scope="session")
async def article(pool: asyncpg.Pool, queue: JobQueue) -> AsyncIterator[Article]:
    yield Article(pool, queue)


@pytest_asyncio.fixture(loop_scope="session")
async def generation_steps(pool: asyncpg.Pool) -> AsyncIterator[GenerationSteps]:
    yield GenerationSteps(pool)


@pytest_asyncio.fixture(autouse=True, loop_scope="session")
async def _clean_tables(pool: asyncpg.Pool, queue: JobQueue, plan: Plan, article: Article) -> None:
    await pool.execute(
        "TRUNCATE TABLE jobs, article_versions, articles, plan_items, plans, "
        "generation_steps RESTART IDENTITY CASCADE"
    )
