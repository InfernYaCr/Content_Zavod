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
`generate_cover` Job (see #6), applied via `apply_cover`. `add_topics`
finds-or-creates the week's non-approved Plan and appends to it, so it
doubles as the retry guard for `generate_plan`'s notification handler
(a retried delivery appends nothing new, since the topics it re-sends were
already appended) and as the entry point for the manual `/topic` command
(see #10) - both are just callers of the same idempotent-per-append
operation.

`request_new` enqueues `generate_plan` itself, keyed on `week_label` alone
(see #7). Because `JobQueue.enqueue` is idempotent, calling it more than
once for the same week - a missed scheduled run followed by a catch-up
run, or a manual trigger racing the schedule - collapses into the same
Job instead of a duplicate Plan; no separate "was this week already
generated" check is needed.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Callable, Sequence
from uuid import uuid4

import asyncpg

from ..job_queue import JobId, JobQueue
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

    async def add_topics(self, week_label: str, topics: Sequence[TopicDraft]) -> PlanId:
        """Append Topics to the week's draft Plan, creating it if none exists yet.

        The one write path for both sourcing modes in ADR-0006: generate_plan's
        notification handler and the manual /topic command both call this with
        their own TopicDraft(s), so whichever fires first creates the week's
        Plan and the other appends to it - neither can silently clobber the
        other's Topics. Targets only the week's non-approved Plan (there is at
        most one, per ADR-0005's single-canonical-Plan model); a proposal for a
        week whose Plan is already approved starts a fresh draft rather than
        reopening a locked one, mirroring the approved-item lock in
        `delete_item`/`regenerate_item`.

        Skips any topic whose title already exists in the target Plan
        (case-insensitively) instead of inserting a duplicate item. This is
        what makes a redelivered `generate_plan` notification retry-safe -
        the same topics it already appended are recognised and skipped - and
        it does so without the old `dedupe_by_week` bail-out that would have
        also swallowed a second caller's genuinely different Topics.
        """
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                existing = await conn.fetchrow(
                    "SELECT id FROM plans WHERE week_label = $1 AND status = 'pending_review'",
                    week_label,
                )
                if existing is not None:
                    plan_id = existing["id"]
                    item_rows = await conn.fetch(
                        "SELECT position, title FROM plan_items WHERE plan_id = $1", plan_id
                    )
                    next_position = max((row["position"] for row in item_rows), default=-1) + 1
                    seen_titles = {row["title"].lower() for row in item_rows}
                else:
                    plan_id = self._new_id()
                    next_position = 0
                    seen_titles = set()
                    await conn.execute(
                        "INSERT INTO plans (id, week_label) VALUES ($1, $2)", plan_id, week_label
                    )
                for topic in topics:
                    if topic.title.lower() in seen_titles:
                        continue
                    seen_titles.add(topic.title.lower())
                    await conn.execute(
                        """
                        INSERT INTO plan_items (id, plan_id, position, title, summary, keywords)
                        VALUES ($1, $2, $3, $4, $5, $6::jsonb)
                        """,
                        self._new_id(),
                        plan_id,
                        next_position,
                        topic.title,
                        topic.summary,
                        _keywords_json(topic.keywords),
                    )
                    next_position += 1
        return PlanId(plan_id)

    async def request_new(self, week_label: str) -> JobId:
        return await self._queue.enqueue(
            "generate_plan",
            {"week_label": week_label},
            # Keyed on week_label alone: repeated triggers for the same week (missed-run
            # catch-up, manual override) collapse into the same Job.
            idempotency_key=f"generate_plan:{week_label}",
        )

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
