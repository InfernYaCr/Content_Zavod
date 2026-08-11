"""Article: Статья (one Площадка's rendering of a Тема) and its Версии.

Status transitions: `queued` -> `generating`* -> `ready` <-> `regenerating` ->
`exported`. (`generating` is a Job Handler concern, see #6 — this module only
ever observes `queued` or the effect of `record_version`.) `record_version`
always appends a new Версия rather than overwriting; `get`/`list_for_plan`
serve the latest one. `mark_exported` and `request_regeneration` are
idempotent on their target state so a retried Telegram callback is a no-op.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Callable, Sequence
from uuid import uuid4

import asyncpg

from ..job_queue import JobQueue
from .errors import ArticleNotFound, ArticleNotReady, ArticleNotRegenerable
from .types import ArticleId, ArticleStatus, ArticleSummary, ArticleView, GeneratedVersion, PlanId, PlanItemId

_SCHEMA_SQL = (Path(__file__).parent / "schema.sql").read_text(encoding="utf-8")

_REGENERABLE_STATUSES: frozenset[ArticleStatus] = frozenset({"ready", "error"})


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

    async def ensure_schema(self) -> None:
        await self._pool.execute(_SCHEMA_SQL)

    async def create(self, plan_id: PlanId, plan_item_id: PlanItemId, title: str, platform: str) -> ArticleId:
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
            "SELECT id FROM articles WHERE plan_item_id = $1 AND platform = $2", plan_item_id, platform
        )
        if existing is None:
            raise RuntimeError(
                f"articles insert conflicted for plan_item_id={plan_item_id!r} platform={platform!r} "
                "but no existing row was found"
            )
        return ArticleId(existing["id"])

    async def get(self, article_id: ArticleId) -> ArticleView:
        article_row = await self._pool.fetchrow(
            "SELECT id, title, platform FROM articles WHERE id = $1", article_id
        )
        if article_row is None:
            raise ArticleNotFound(article_id)
        content = await self._latest_content(article_id)
        if content is None:
            raise ArticleNotReady(article_id)
        return _to_view(article_id, article_row["title"], article_row["platform"], content)

    async def list_for_plan(self, plan_id: PlanId) -> list[ArticleView]:
        rows = await self._pool.fetch(
            "SELECT id, title, platform FROM articles WHERE plan_id = $1 ORDER BY created_at",
            plan_id,
        )
        views: list[ArticleView] = []
        for row in rows:
            article_id = ArticleId(row["id"])
            content = await self._latest_content(article_id)
            if content is None:
                continue
            views.append(_to_view(article_id, row["title"], row["platform"], content))
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
            ArticleSummary(id=ArticleId(row["id"]), title=row["title"], platform=row["platform"], status=row["status"])
            for row in rows
        ]

    async def record_version(self, article_id: ArticleId, version: GeneratedVersion) -> None:
        article_row = await self._pool.fetchrow("SELECT id FROM articles WHERE id = $1", article_id)
        if article_row is None:
            raise ArticleNotFound(article_id)
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    """
                    INSERT INTO article_versions (article_id, content, prompt, model, tokens, cost)
                    VALUES ($1, $2, $3, $4, $5, $6)
                    """,
                    article_id,
                    version.content,
                    version.prompt,
                    version.model,
                    version.tokens,
                    version.cost,
                )
                await conn.execute(
                    "UPDATE articles SET status = 'ready', updated_at = now() WHERE id = $1", article_id
                )

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
        await self._queue.enqueue(
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
                await conn.execute(
                    "UPDATE articles SET status = 'regenerating', updated_at = now() WHERE id = $1",
                    article_id,
                )
                await self._queue.enqueue(
                    "regenerate_article",
                    {"article_id": article_id, "comment": comment},
                    # Keyed on the pre-update updated_at so two racing calls collapse
                    # into the same job instead of enqueuing a duplicate.
                    idempotency_key=f"regenerate_article:{article_id}:{row['updated_at'].isoformat()}",
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


def _to_view(article_id: ArticleId, title: str, platform: str, content: str) -> ArticleView:
    return ArticleView(
        id=article_id,
        title=title,
        platform=platform,
        filename=_build_filename(title, platform),
        content=content.encode("utf-8"),
    )


def _build_filename(title: str, platform: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-") or "article"
    return f"{slug}-{platform}.txt"
