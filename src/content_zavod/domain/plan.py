"""Plan: План (a week's worth of Темы) and its items.

Status transitions per item: `pending_review` -> `approved` | `rejected`.
Once an item is `approved` it is locked against `delete_item`/`regenerate_item`
(`PlanItemNotEditable`); `delete_item`/`approve_all` are idempotent so a
retried Telegram callback is a no-op rather than an error.

`regenerate_item` does not call an LLM directly: it enqueues a
`regenerate_topic` Job (ADR-0004) and returns immediately. The result comes
back asynchronously via `apply_regeneration`, called by the notification
handler once the job completes (see job_queue.run_notifications).

`request_cover` follows the same enqueue-now/apply-later shape for the
`generate_cover` Job (see #6), applied via `apply_cover`. `create`'s
`dedupe_by_week` guards `generate_plan`'s notification handler against
re-persisting the same week's Plan on a retried delivery.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Callable, Sequence
from uuid import uuid4

import asyncpg

from ..job_queue import JobQueue
from .errors import PlanItemNotEditable, PlanItemNotFound, PlanNotFound
from .types import PlanId, PlanItemId, PlanItemStatus, PlanItemView, PlanView, TopicDraft

_SCHEMA_SQL = (Path(__file__).parent / "schema.sql").read_text(encoding="utf-8")


class Plan:
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

    async def get(self, plan_id: PlanId) -> PlanView:
        plan_row = await self._pool.fetchrow("SELECT id, week_label FROM plans WHERE id = $1", plan_id)
        if plan_row is None:
            raise PlanNotFound(plan_id)
        item_rows = await self._pool.fetch(
            "SELECT id, title, status FROM plan_items WHERE plan_id = $1 ORDER BY position",
            plan_id,
        )
        items = [
            PlanItemView(id=PlanItemId(row["id"]), title=row["title"], status=row["status"])
            for row in item_rows
        ]
        return PlanView(id=PlanId(plan_row["id"]), week_label=plan_row["week_label"], items=items)

    async def create(
        self, week_label: str, topics: list[TopicDraft], *, dedupe_by_week: bool = False
    ) -> PlanId:
        if dedupe_by_week:
            # Guards against a retried generate_plan notification re-persisting the
            # same week's Plan (e.g. after `handle` succeeded but the queue's
            # mark_notified failed to commit).
            existing = await self._pool.fetchrow("SELECT id FROM plans WHERE week_label = $1", week_label)
            if existing is not None:
                return PlanId(existing["id"])
        plan_id = self._new_id()
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    "INSERT INTO plans (id, week_label) VALUES ($1, $2)", plan_id, week_label
                )
                for position, topic in enumerate(topics):
                    await conn.execute(
                        """
                        INSERT INTO plan_items (id, plan_id, position, title, summary, keywords)
                        VALUES ($1, $2, $3, $4, $5, $6::jsonb)
                        """,
                        self._new_id(),
                        plan_id,
                        position,
                        topic.title,
                        topic.summary,
                        _keywords_json(topic.keywords),
                    )
        return PlanId(plan_id)

    async def delete_item(self, plan_item_id: PlanItemId) -> None:
        status = await self._item_status(plan_item_id)
        if status == "rejected":
            return
        if status == "approved":
            raise PlanItemNotEditable(plan_item_id, status)
        await self._pool.execute(
            "UPDATE plan_items SET status = 'rejected', updated_at = now() WHERE id = $1",
            plan_item_id,
        )

    async def regenerate_item(self, plan_item_id: PlanItemId, comment: str | None) -> None:
        row = await self._pool.fetchrow(
            "SELECT status, updated_at FROM plan_items WHERE id = $1", plan_item_id
        )
        if row is None:
            raise PlanItemNotFound(plan_item_id)
        if row["status"] != "pending_review":
            raise PlanItemNotEditable(plan_item_id, row["status"])
        await self._queue.enqueue(
            "regenerate_topic",
            {"plan_item_id": plan_item_id, "comment": comment},
            # Keyed on the item's current updated_at so a retried callback (same item,
            # unchanged since) collapses into the same job instead of enqueuing a duplicate.
            idempotency_key=f"regenerate_topic:{plan_item_id}:{row['updated_at'].isoformat()}",
        )

    async def approve_all(self, plan_id: PlanId) -> None:
        plan_row = await self._pool.fetchrow("SELECT status FROM plans WHERE id = $1", plan_id)
        if plan_row is None:
            raise PlanNotFound(plan_id)
        if plan_row["status"] == "approved":
            return
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    "UPDATE plans SET status = 'approved', updated_at = now() WHERE id = $1", plan_id
                )
                await conn.execute(
                    """
                    UPDATE plan_items SET status = 'approved', updated_at = now()
                    WHERE plan_id = $1 AND status = 'pending_review'
                    """,
                    plan_id,
                )

    async def apply_regeneration(self, plan_item_id: PlanItemId, topic: TopicDraft) -> None:
        await self._pool.execute(
            """
            UPDATE plan_items
            SET title = $2, summary = $3, keywords = $4::jsonb, updated_at = now()
            WHERE id = $1 AND status = 'pending_review'
            """,
            plan_item_id,
            topic.title,
            topic.summary,
            _keywords_json(topic.keywords),
        )

    async def request_cover(self, plan_item_id: PlanItemId) -> None:
        row = await self._pool.fetchrow(
            "SELECT title, summary, updated_at FROM plan_items WHERE id = $1", plan_item_id
        )
        if row is None:
            raise PlanItemNotFound(plan_item_id)
        await self._queue.enqueue(
            "generate_cover",
            {"plan_item_id": plan_item_id, "title": row["title"], "summary": row["summary"]},
            # Keyed on the item's current updated_at so a retried callback (same item,
            # unchanged since) collapses into the same job instead of enqueuing a duplicate.
            idempotency_key=f"generate_cover:{plan_item_id}:{row['updated_at'].isoformat()}",
        )

    async def apply_cover(self, plan_item_id: PlanItemId, image: bytes, mime_type: str) -> None:
        await self._pool.execute(
            """
            UPDATE plan_items
            SET cover_image = $2, cover_mime_type = $3, cover_generated_at = now()
            WHERE id = $1
            """,
            plan_item_id,
            image,
            mime_type,
        )

    async def recent_topic_titles(self, since: datetime) -> list[str]:
        rows = await self._pool.fetch(
            "SELECT DISTINCT title FROM plan_items WHERE created_at >= $1", since
        )
        return [row["title"] for row in rows]

    async def _item_status(self, plan_item_id: PlanItemId) -> PlanItemStatus:
        row = await self._pool.fetchrow("SELECT status FROM plan_items WHERE id = $1", plan_item_id)
        if row is None:
            raise PlanItemNotFound(plan_item_id)
        return row["status"]


def _keywords_json(keywords: Sequence[str]) -> str:
    return json.dumps(list(keywords))
