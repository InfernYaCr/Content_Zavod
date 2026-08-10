"""JobQueue: a Postgres-backed job queue.

Idempotent enqueue (unique `idempotency_key`), atomic claim via
`SELECT ... FOR UPDATE SKIP LOCKED` so multiple workers can pull from the
same table safely, exponential-backoff retries up to a limit, and recovery
of `running` jobs abandoned by a crashed worker. Job result and status are
written in the same statement, so a job is never `done`/`failed` without
its result.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any

import asyncpg

from .errors import JobNotFound, JobQueueError
from .models import JobId, JobResult, JobStatus

_SCHEMA_SQL = (Path(__file__).parent / "schema.sql").read_text(encoding="utf-8")


def _exponential_delay(base_delay: float, attempts: int) -> float:
    return base_delay * (2 ** (attempts - 1))


@dataclass(frozen=True)
class ClaimedJob:
    id: JobId
    job_type: str
    payload: dict[str, Any]
    attempts: int


@dataclass(frozen=True)
class ClaimedNotification:
    result: JobResult
    notification_attempts: int


class JobQueue:
    def __init__(
        self,
        pool: asyncpg.Pool,
        *,
        max_attempts: int = 5,
        base_delay: float = 1.0,
        notification_max_attempts: int = 5,
        notification_base_delay: float = 1.0,
        stuck_timeout: timedelta = timedelta(minutes=10),
    ) -> None:
        self._pool = pool
        self._max_attempts = max_attempts
        self._base_delay = base_delay
        self._notification_max_attempts = notification_max_attempts
        self._notification_base_delay = notification_base_delay
        self._stuck_timeout = stuck_timeout

    async def ensure_schema(self) -> None:
        await self._pool.execute(_SCHEMA_SQL)

    async def enqueue(self, job_type: str, payload: dict[str, Any], idempotency_key: str) -> JobId:
        row = await self._pool.fetchrow(
            """
            INSERT INTO jobs (job_type, payload, idempotency_key)
            VALUES ($1, $2::jsonb, $3)
            ON CONFLICT (idempotency_key) DO UPDATE SET idempotency_key = EXCLUDED.idempotency_key
            RETURNING id
            """,
            job_type,
            json.dumps(payload),
            idempotency_key,
        )
        if row is None:
            raise JobQueueError(f"enqueue failed to return an id for idempotency_key={idempotency_key!r}")
        return JobId(row["id"])

    async def get_status(self, job_id: JobId) -> JobStatus:
        row = await self._pool.fetchrow("SELECT status FROM jobs WHERE id = $1", job_id)
        if row is None:
            raise JobNotFound(job_id)
        return row["status"]

    async def claim_next(self) -> ClaimedJob | None:
        row = await self._pool.fetchrow(
            """
            UPDATE jobs
            SET status = 'running', attempts = attempts + 1, locked_at = now(), updated_at = now()
            WHERE id = (
                SELECT id FROM jobs
                WHERE status = 'queued' AND run_at <= now()
                ORDER BY run_at
                FOR UPDATE SKIP LOCKED
                LIMIT 1
            )
            RETURNING id, job_type, payload, attempts
            """
        )
        if row is None:
            return None
        return ClaimedJob(
            id=JobId(row["id"]),
            job_type=row["job_type"],
            payload=json.loads(row["payload"]),
            attempts=row["attempts"],
        )

    async def complete(self, job_id: JobId, output: dict[str, Any]) -> None:
        await self._pool.execute(
            """
            UPDATE jobs
            SET status = 'done', output = $2::jsonb, error = NULL, locked_at = NULL, updated_at = now()
            WHERE id = $1
            """,
            job_id,
            json.dumps(output),
        )

    async def fail(self, job_id: JobId, error: str) -> None:
        row = await self._pool.fetchrow("SELECT attempts FROM jobs WHERE id = $1", job_id)
        if row is None:
            raise JobNotFound(job_id)
        attempts = row["attempts"]
        if attempts < self._max_attempts:
            delay = _exponential_delay(self._base_delay, attempts)
            await self._pool.execute(
                """
                UPDATE jobs
                SET status = 'queued', error = $2, locked_at = NULL,
                    run_at = now() + $3 * interval '1 second', updated_at = now()
                WHERE id = $1
                """,
                job_id,
                error,
                delay,
            )
        else:
            await self._pool.execute(
                """
                UPDATE jobs
                SET status = 'failed', error = $2, locked_at = NULL, updated_at = now()
                WHERE id = $1
                """,
                job_id,
                error,
            )

    async def retry(self, job_id: JobId) -> None:
        """Reset a failed job back to queued, for a user-triggered "Повторить" retry.

        Also clears `notified_at` so the eventual re-completion (done or
        failed again) is notified once more - otherwise `claim_notification`
        would skip it as already-notified from the original failure.
        """
        result = await self._pool.execute(
            """
            UPDATE jobs
            SET status = 'queued', attempts = 0, error = NULL, run_at = now(),
                locked_at = NULL, notified_at = NULL, updated_at = now()
            WHERE id = $1
            """,
            job_id,
        )
        if result == "UPDATE 0":
            raise JobNotFound(job_id)

    async def recover_stuck(self) -> int:
        timeout_seconds = self._stuck_timeout.total_seconds()
        rows = await self._pool.fetch(
            """
            UPDATE jobs
            SET status = 'queued', locked_at = NULL, updated_at = now()
            WHERE status = 'running' AND locked_at < now() - $1 * interval '1 second'
            RETURNING id
            """,
            timeout_seconds,
        )
        await self._pool.execute(
            """
            UPDATE jobs
            SET notify_locked_at = NULL, updated_at = now()
            WHERE notify_locked_at IS NOT NULL AND notify_locked_at < now() - $1 * interval '1 second'
            """,
            timeout_seconds,
        )
        return len(rows)

    async def claim_notification(self) -> ClaimedNotification | None:
        row = await self._pool.fetchrow(
            """
            UPDATE jobs
            SET notification_attempts = notification_attempts + 1, notify_locked_at = now()
            WHERE id = (
                SELECT id FROM jobs
                WHERE status IN ('done', 'failed') AND notified_at IS NULL
                    AND notify_locked_at IS NULL AND run_at <= now()
                ORDER BY run_at
                FOR UPDATE SKIP LOCKED
                LIMIT 1
            )
            RETURNING id, job_type, status, output, error, notification_attempts
            """
        )
        if row is None:
            return None
        result = JobResult(
            job_id=JobId(row["id"]),
            job_type=row["job_type"],
            status=row["status"],
            output=json.loads(row["output"]) if row["output"] is not None else None,
            error=row["error"],
        )
        return ClaimedNotification(result=result, notification_attempts=row["notification_attempts"])

    async def mark_notified(self, job_id: JobId) -> None:
        await self._pool.execute(
            "UPDATE jobs SET notified_at = now(), notify_locked_at = NULL WHERE id = $1", job_id
        )

    async def reschedule_notification(self, job_id: JobId, notification_attempts: int) -> None:
        delay = _exponential_delay(self._notification_base_delay, notification_attempts)
        await self._pool.execute(
            """
            UPDATE jobs
            SET run_at = now() + $2 * interval '1 second', notify_locked_at = NULL
            WHERE id = $1
            """,
            job_id,
            delay,
        )

    def notification_attempts_exhausted(self, notification_attempts: int) -> bool:
        return notification_attempts >= self._notification_max_attempts
