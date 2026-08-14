"""worker_main: the process that runs Job Handlers against the Job Queue (ADR-0004).

Wires the already-tested pieces together: one `asyncpg.Pool`, `JobQueue`,
`Plan`/`Article` (used here only as the readers `regenerate_topic`/
`regenerate_article` need, per ADR-0004's "notification handler applies the
result" split — this process never writes Plan/Article state itself), the
Yandex clients, and the Job Handler factories from `pipelines/`, then runs
`run_worker` until SIGINT/SIGTERM.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

import asyncpg

from ..config import Settings, YandexCredentials, load_settings
from ..domain import Article, GenerationSteps, Plan
from ..job_queue import ClaimedJob, JobHandler, JobPartialFailure, JobQueue, run_worker
from ..migrations import run_migrations
from ..owner_settings import OwnerSettingsStore
from ..pipelines import (
    HttpxUrlReachabilityChecker,
    make_generate_article_handler,
    make_generate_cover_handler,
    make_generate_plan_handler,
    make_regenerate_article_handler,
    make_regenerate_topic_handler,
)
from ..settings import SettingsService
from ..yandex import ImageGenerator, KeywordStats, TextGenerator
from ._process import register_shutdown

logger = logging.getLogger(__name__)


def _build_yandex_client[T](
    yandex: YandexCredentials,
    with_service_account_key: Callable[..., T],
    with_oauth_token: Callable[..., T],
    **extra: Any,
) -> T:
    """Every Yandex client (TextGenerator/ImageGenerator/KeywordStats) offers the same
    two constructors; build whichever credential Settings resolved to."""
    if yandex.api_key is not None:
        return with_service_account_key(yandex.api_key, folder_id=yandex.folder_id, **extra)
    assert yandex.oauth_token is not None
    return with_oauth_token(yandex.oauth_token, folder_id=yandex.folder_id, **extra)


def _make_on_attempt(generation_steps: GenerationSteps) -> Callable[..., Any]:
    """Persists every attempt's provenance/cost (#74) right after `run_worker` settles
    it - `output`/`error` on success/failure alike carry a `steps` list built by the
    pipeline's `StepRecorder`, so a Job's cost reflects every attempt including ones
    that failed and got retried, not only the last one that happened to succeed."""

    async def on_attempt(
        claimed: ClaimedJob, output: dict[str, Any] | None, error: BaseException | None
    ) -> None:
        if output is not None:
            steps = output.get("steps") or []
            article_id = output.get("article_id")
        elif isinstance(error, JobPartialFailure):
            steps = error.partial_output.get("steps") or []
            article_id = error.partial_output.get("article_id")
        else:
            return
        if not steps:
            return
        await generation_steps.record_many(
            job_id=claimed.id, job_type=claimed.job_type, article_id=article_id, steps=steps
        )

    return on_attempt


async def main(settings: Settings | None = None) -> None:
    logging.basicConfig(level=logging.INFO)
    settings = settings or load_settings()

    pool = await asyncpg.create_pool(dsn=settings.postgres_dsn)
    assert pool is not None
    try:
        await run_migrations(pool)

        queue = JobQueue(pool)
        plan = Plan(pool, queue)
        article = Article(pool, queue)
        owner_settings = OwnerSettingsStore(pool)
        owner_settings_service = SettingsService(owner_settings)
        generation_steps = GenerationSteps(pool)

        text_generator = _build_yandex_client(
            settings.yandex,
            TextGenerator.with_service_account_key,
            TextGenerator.with_oauth_token,
            cost_per_1k_tokens=settings.yandex_pricing.text_cost_per_1k_tokens,
        )
        image_generator = _build_yandex_client(
            settings.yandex,
            ImageGenerator.with_service_account_key,
            ImageGenerator.with_oauth_token,
            cost_per_generation=settings.yandex_pricing.image_cost_per_generation,
        )
        keyword_stats = _build_yandex_client(
            settings.yandex, KeywordStats.with_service_account_key, KeywordStats.with_oauth_token
        )
        url_checker = HttpxUrlReachabilityChecker()

        handlers: dict[str, JobHandler] = {
            "generate_plan": make_generate_plan_handler(
                keyword_stats, text_generator, plan.recent_topic_titles, owner_settings_service
            ),
            "generate_article": make_generate_article_handler(
                text_generator, url_checker, owner_settings_service
            ),
            "regenerate_article": make_regenerate_article_handler(
                article, text_generator, url_checker, owner_settings_service
            ),
            "generate_cover": make_generate_cover_handler(image_generator),
            "regenerate_topic": make_regenerate_topic_handler(
                plan, text_generator, owner_settings_service
            ),
        }

        stop = asyncio.Event()
        register_shutdown(stop)
        logger.info("worker started")
        try:
            await run_worker(
                queue, handlers, stop=stop, on_attempt=_make_on_attempt(generation_steps)
            )
        finally:
            await url_checker.aclose()
    finally:
        await pool.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
