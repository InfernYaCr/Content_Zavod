"""Article: Статья (one Площадка's rendering of a Тема) and its Версии.

Status transitions: `queued` -> `generating`* -> `ready` <-> `regenerating` ->
`exported`. (`generating` is a Job Handler concern, see #6 — this module only
ever observes `queued` or the effect of `record_version`.) `record_version`
always appends a new Версия rather than overwriting; `get`/`list_for_plan`
serve the latest one. `mark_exported` and `request_regeneration` are
idempotent on their target state so a retried Telegram callback is a no-op.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Literal
from uuid import uuid4

import asyncpg

from ..job_queue import JobQueue
from .errors import ArticleNotFound, ArticleNotReady, ArticleNotRegenerable, ArticleVersionNotFound
from .types import (
    ArticleId,
    ArticleStatus,
    ArticleSummary,
    ArticleVersionSummary,
    ArticleVersionView,
    ArticleView,
    GeneratedVersion,
    PlanId,
    PlanItemId,
)

_REGENERABLE_STATUSES: frozenset[ArticleStatus] = frozenset({"ready", "error"})

GenerationResultApplication = Literal["applied", "already_applied", "stale"]


class Article:
    def __init__(
        self,
        pool: asyncpg.Pool,
        queue: JobQueue,
        *,
        new_id: Callable[[], str] = lambda: uuid4().hex,
    ) -> None:
        self._pool = pool
        self._queue = queue
        self._new_id = new_id

    async def create(
        self, plan_id: PlanId, plan_item_id: PlanItemId, title: str, platform: str
    ) -> ArticleId:
        """Idempotent on (plan_item_id, platform): a second call for the same pair reuses the
        existing Статья's id instead of inserting a duplicate row (#14)."""
        row = await self._pool.fetchrow(
            """
            INSERT INTO articles (id, plan_id, plan_item_id, title, platform)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (plan_item_id, platform) DO NOTHING
            RETURNING id
            """,
            self._new_id(),
            plan_id,
            plan_item_id,
            title,
            platform,
        )
        if row is not None:
            return ArticleId(row["id"])
        existing = await self._pool.fetchrow(
            "SELECT id FROM articles WHERE plan_item_id = $1 AND platform = $2",
            plan_item_id,
            platform,
        )
        if existing is None:
            raise RuntimeError(
                f"articles insert conflicted for plan_item_id={plan_item_id!r} platform={platform!r} "
                "but no existing row was found"
            )
        return ArticleId(existing["id"])

    async def get(self, article_id: ArticleId) -> ArticleView:
        article_row = await self._pool.fetchrow(
            "SELECT id, plan_item_id, title, platform FROM articles WHERE id = $1", article_id
        )
        if article_row is None:
            raise ArticleNotFound(article_id)
        content = await self._latest_content(article_id)
        if content is None:
            raise ArticleNotReady(article_id)
        return _to_view(
            article_id,
            PlanItemId(article_row["plan_item_id"]),
            article_row["title"],
            article_row["platform"],
            content,
        )

    async def list_for_plan(self, plan_id: PlanId) -> list[ArticleView]:
        rows = await self._pool.fetch(
            "SELECT id, plan_item_id, title, platform FROM articles WHERE plan_id = $1 ORDER BY created_at",
            plan_id,
        )
        views: list[ArticleView] = []
        for row in rows:
            article_id = ArticleId(row["id"])
            content = await self._latest_content(article_id)
            if content is None:
                continue
            views.append(
                _to_view(
                    article_id,
                    PlanItemId(row["plan_item_id"]),
                    row["title"],
                    row["platform"],
                    content,
                )
            )
        return views

    async def list_summary_for_plan(self, plan_id: PlanId) -> list[ArticleSummary]:
        """Every Статья for a Plan, whatever its status - unlike `list_for_plan`, doesn't skip
        ones with no generated content yet. Powers /history's article list (#29), where a
        `queued`/`generating`/`error` Статья must still show up with a status label."""
        rows = await self._pool.fetch(
            "SELECT id, title, platform, status FROM articles WHERE plan_id = $1 ORDER BY created_at",
            plan_id,
        )
        return [
            ArticleSummary(
                id=ArticleId(row["id"]),
                title=row["title"],
                platform=row["platform"],
                status=row["status"],
            )
            for row in rows
        ]

    async def get_summary(self, article_id: ArticleId) -> ArticleSummary:
        row = await self._pool.fetchrow(
            "SELECT id, title, platform, status FROM articles WHERE id = $1", article_id
        )
        if row is None:
            raise ArticleNotFound(article_id)
        return ArticleSummary(
            id=ArticleId(row["id"]),
            title=row["title"],
            platform=row["platform"],
            status=row["status"],
        )

    async def get_plan_id(self, article_id: ArticleId) -> PlanId:
        row = await self._pool.fetchrow("SELECT plan_id FROM articles WHERE id = $1", article_id)
        if row is None:
            raise ArticleNotFound(article_id)
        return PlanId(row["plan_id"])

    async def list_versions(self, article_id: ArticleId) -> list[ArticleVersionSummary]:
        """Every Версия's metadata, newest first, for /history's version list (#26)."""
        article_row = await self._pool.fetchrow("SELECT id FROM articles WHERE id = $1", article_id)
        if article_row is None:
            raise ArticleNotFound(article_id)
        rows = await self._pool.fetch(
            "SELECT id, model, tokens, cost, created_at FROM article_versions "
            "WHERE article_id = $1 ORDER BY created_at DESC, id DESC",
            article_id,
        )
        return [
            ArticleVersionSummary(
                id=row["id"],
                model=row["model"],
                tokens=row["tokens"],
                cost=float(row["cost"]) if row["cost"] is not None else None,
                created_at=row["created_at"],
            )
            for row in rows
        ]

    async def get_version(self, article_id: ArticleId, version_id: int) -> ArticleVersionView:
        """One Версия's full content, for /history's version detail screen (#26)."""
        row = await self._pool.fetchrow(
            "SELECT id, content, model, tokens, cost, created_at FROM article_versions "
            "WHERE article_id = $1 AND id = $2",
            article_id,
            version_id,
        )
        if row is None:
            raise ArticleVersionNotFound(article_id, version_id)
        return ArticleVersionView(
            id=row["id"],
            content=row["content"],
            model=row["model"],
            tokens=row["tokens"],
            cost=float(row["cost"]) if row["cost"] is not None else None,
            created_at=row["created_at"],
        )

    async def record_version(
        self, article_id: ArticleId, version: GeneratedVersion
    ) -> GenerationResultApplication:
        """Apply one finished generation job exactly once.

        A source job is the application receipt. Replaying its notification returns
        ``already_applied`` without appending another Version. A result from a job
        superseded by a newer generation returns ``stale`` and cannot overwrite it.
        Versions created by older callers without ``source_job_id`` retain the legacy
        append behavior.
        """
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                article_row = await conn.fetchrow(
                    "SELECT active_generation_job_id FROM articles WHERE id = $1 FOR UPDATE",
                    article_id,
                )
                if article_row is None:
                    raise ArticleNotFound(article_id)
                if version.source_job_id is not None:
                    existing = await conn.fetchrow(
                        "SELECT article_id FROM article_versions WHERE source_job_id = $1",
                        version.source_job_id,
                    )
                    if existing is not None:
                        return (
                            "already_applied" if existing["article_id"] == article_id else "stale"
                        )
                    if article_row["active_generation_job_id"] != version.source_job_id:
                        return "stale"
                await conn.execute(
                    """
                    INSERT INTO article_versions
                        (article_id, content, prompt, model, tokens, cost, source_job_id)
                    VALUES ($1, $2, $3, $4, $5, $6, $7)
                    """,
                    article_id,
                    version.content,
                    version.prompt,
                    version.model,
                    version.tokens,
                    version.cost,
                    version.source_job_id,
                )
                await conn.execute(
                    "UPDATE articles SET status = 'ready', active_generation_job_id = NULL, "
                    "updated_at = now() WHERE id = $1",
                    article_id,
                )
        return "applied"

    async def mark_generation_failed(self, source_job_id: int) -> ArticleId | None:
        """Move only the Article still owned by this final failed job to ``error``."""
        row = await self._pool.fetchrow(
            """
            UPDATE articles
            SET status = 'error', updated_at = now()
            WHERE active_generation_job_id = $1
            RETURNING id
            """,
            source_job_id,
        )
        return ArticleId(row["id"]) if row is not None else None

    async def request_generation(
        self,
        plan_id: PlanId,
        plan_item_id: PlanItemId,
        title: str,
        summary: str,
        keywords: Sequence[str],
        platform: str,
    ) -> ArticleId:
        """Create (or reuse) the Статья for one (Тема, Площадка) pair and enqueue its `generate_article`
        Job. Fully idempotent: `create` reuses the existing row on a (plan_item_id, platform)
        conflict, and the Job enqueue is keyed on the resulting article_id - so replaying this for
        an already-processed pair (a retried `approve_all` callback, or a crash between approving
        the Plan and enqueueing) creates neither a duplicate Статья nor a duplicate Job (#14)."""
        article_id = await self.create(plan_id, plan_item_id, title, platform)
        job_id = await self._queue.enqueue(
            "generate_article",
            {
                "article_id": article_id,
                "title": title,
                "platform": platform,
                "summary": summary,
                "keywords": list(keywords),
            },
            idempotency_key=f"generate_article:{article_id}",
        )
        await self._pool.execute(
            """
            UPDATE articles
            SET active_generation_job_id = $2, updated_at = now()
            WHERE id = $1 AND status = 'queued'
            """,
            article_id,
            job_id,
        )
        return article_id

    async def request_regeneration(self, article_id: ArticleId, comment: str | None) -> None:
        row = await self._pool.fetchrow(
            "SELECT status, updated_at FROM articles WHERE id = $1", article_id
        )
        if row is None:
            raise ArticleNotFound(article_id)
        if row["status"] == "regenerating":
            return
        if row["status"] not in _REGENERABLE_STATUSES:
            raise ArticleNotRegenerable(article_id, row["status"])
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                job_id = await self._queue.enqueue(
                    "regenerate_article",
                    {"article_id": article_id, "comment": comment},
                    # Keyed on the pre-update updated_at so two racing calls collapse
                    # into the same job instead of enqueuing a duplicate.
                    idempotency_key=f"regenerate_article:{article_id}:{row['updated_at'].isoformat()}",
                )
                await conn.execute(
                    """
                    UPDATE articles
                    SET status = 'regenerating', active_generation_job_id = $2, updated_at = now()
                    WHERE id = $1
                    """,
                    article_id,
                    job_id,
                )

    async def mark_exported(self, article_id: ArticleId) -> None:
        status = await self._article_status(article_id)
        if status == "exported":
            return
        if status != "ready":
            raise ArticleNotReady(article_id)
        await self._pool.execute(
            "UPDATE articles SET status = 'exported', updated_at = now() WHERE id = $1", article_id
        )

    async def _article_status(self, article_id: ArticleId) -> ArticleStatus:
        row = await self._pool.fetchrow("SELECT status FROM articles WHERE id = $1", article_id)
        if row is None:
            raise ArticleNotFound(article_id)
        return row["status"]

    async def _latest_content(self, article_id: ArticleId) -> str | None:
        row = await self._pool.fetchrow(
            "SELECT content FROM article_versions WHERE article_id = $1 ORDER BY created_at DESC, id DESC LIMIT 1",
            article_id,
        )
        return row["content"] if row is not None else None


def _to_view(
    article_id: ArticleId, plan_item_id: PlanItemId, title: str, platform: str, content: str
) -> ArticleView:
    return ArticleView(
        id=article_id,
        plan_item_id=plan_item_id,
        title=title,
        platform=platform,
        content=content.encode("utf-8"),
    )
