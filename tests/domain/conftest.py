from __future__ import annotations

from datetime import timedelta
from typing import AsyncIterator

import asyncpg
import pytest
import pytest_asyncio
from testcontainers.community.postgres import PostgresContainer

from content_zavod.domain import Article, Plan
from content_zavod.job_queue import JobQueue


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
async def queue(pool: asyncpg.Pool) -> AsyncIterator[JobQueue]:
    job_queue = JobQueue(
        pool,
        base_delay=0.01,
        notification_base_delay=0.01,
        stuck_timeout=timedelta(seconds=0),
    )
    await job_queue.ensure_schema()
    yield job_queue


@pytest_asyncio.fixture(loop_scope="session")
async def plan(pool: asyncpg.Pool, queue: JobQueue) -> AsyncIterator[Plan]:
    domain_plan = Plan(pool, queue)
    await domain_plan.ensure_schema()
    yield domain_plan


@pytest_asyncio.fixture(loop_scope="session")
async def article(pool: asyncpg.Pool, queue: JobQueue) -> AsyncIterator[Article]:
    domain_article = Article(pool, queue)
    await domain_article.ensure_schema()
    yield domain_article


@pytest_asyncio.fixture(autouse=True, loop_scope="session")
async def _clean_tables(pool: asyncpg.Pool, queue: JobQueue, plan: Plan, article: Article) -> None:
    await pool.execute(
        "TRUNCATE TABLE jobs, article_versions, articles, plan_items, plans RESTART IDENTITY CASCADE"
    )
