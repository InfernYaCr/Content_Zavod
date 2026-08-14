"""GenerationSteps: the append-only provenance ledger behind #74 - one row per
LLM/image call made while running a Job (generate_plan, regenerate_topic,
generate_article, regenerate_article, generate_cover), across every attempt.

Written by the worker itself right after each attempt settles (see
`pipelines.provenance.StepRecorder` and `job_queue.run_worker`'s
`on_attempt` hook), not by the async notification path - a Job that gets
retried still has its earlier, failed attempts' steps recorded, so
`job_cost` sums the true cost of the Job, not just its last attempt.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import asyncpg


@dataclass(frozen=True)
class GenerationStepView:
    id: int
    job_id: int
    job_type: str
    article_id: str | None
    step_name: str
    provider: str
    model: str
    params: dict[str, Any]
    prompt_template_version: str
    prompt_hash: str
    tokens: int | None
    usage_missing: bool
    latency_ms: int
    cost: float | None
    created_at: datetime


@dataclass(frozen=True)
class JobCostSummary:
    """A Job's cost as the sum of every recorded step across every attempt.

    `complete` is False when any step's cost is unknown (missing usage data
    or unconfigured pricing) - `total` then undercounts, so callers should
    surface that gap rather than presenting `total` as the final number.
    """

    total: float
    complete: bool
    step_count: int


class GenerationSteps:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def record_many(
        self,
        *,
        job_id: int,
        job_type: str,
        article_id: str | None,
        steps: list[dict[str, Any]],
    ) -> None:
        if not steps:
            return
        await self._pool.executemany(
            """
            INSERT INTO generation_steps
                (job_id, job_type, article_id, step_name, provider, model, params,
                 prompt_template_version, prompt_hash, tokens, usage_missing,
                 latency_ms, cost)
            VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8, $9, $10, $11, $12, $13)
            """,
            [
                (
                    job_id,
                    job_type,
                    article_id,
                    step["step_name"],
                    step["provider"],
                    step["model"],
                    json.dumps(step["params"]),
                    step["prompt_template_version"],
                    step["prompt_hash"],
                    step["tokens"],
                    step["usage_missing"],
                    step["latency_ms"],
                    step["cost"],
                )
                for step in steps
            ],
        )

    async def job_cost(self, job_id: int) -> JobCostSummary:
        row = await self._pool.fetchrow(
            """
            SELECT
                COALESCE(SUM(cost), 0) AS total,
                COUNT(*) FILTER (WHERE cost IS NULL) AS missing,
                COUNT(*) AS step_count
            FROM generation_steps
            WHERE job_id = $1
            """,
            job_id,
        )
        assert row is not None
        return JobCostSummary(
            total=float(row["total"]),
            complete=row["missing"] == 0,
            step_count=row["step_count"],
        )

    async def list_for_article(self, article_id: str) -> list[GenerationStepView]:
        rows = await self._pool.fetch(
            """
            SELECT id, job_id, job_type, article_id, step_name, provider, model, params,
                   prompt_template_version, prompt_hash, tokens, usage_missing,
                   latency_ms, cost, created_at
            FROM generation_steps
            WHERE article_id = $1
            ORDER BY created_at
            """,
            article_id,
        )
        return [_to_view(row) for row in rows]

    async def list_for_job(self, job_id: int) -> list[GenerationStepView]:
        rows = await self._pool.fetch(
            """
            SELECT id, job_id, job_type, article_id, step_name, provider, model, params,
                   prompt_template_version, prompt_hash, tokens, usage_missing,
                   latency_ms, cost, created_at
            FROM generation_steps
            WHERE job_id = $1
            ORDER BY created_at
            """,
            job_id,
        )
        return [_to_view(row) for row in rows]


def _to_view(row: asyncpg.Record) -> GenerationStepView:
    return GenerationStepView(
        id=row["id"],
        job_id=row["job_id"],
        job_type=row["job_type"],
        article_id=row["article_id"],
        step_name=row["step_name"],
        provider=row["provider"],
        model=row["model"],
        params=json.loads(row["params"]),
        prompt_template_version=row["prompt_template_version"],
        prompt_hash=row["prompt_hash"],
        tokens=row["tokens"],
        usage_missing=row["usage_missing"],
        latency_ms=row["latency_ms"],
        cost=float(row["cost"]) if row["cost"] is not None else None,
        created_at=row["created_at"],
    )
