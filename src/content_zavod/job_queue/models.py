"""Types shared across the job queue: identifiers, status, handler contract, and results."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Literal

JobId = int

JobStatus = Literal["queued", "running", "done", "failed"]

# A handler receives the job's payload and returns its output, or raises to signal failure.
JobHandler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


@dataclass(frozen=True)
class JobResult:
    job_id: JobId
    job_type: str
    status: Literal["done", "failed"]
    output: dict[str, Any] | None = None
    error: str | None = None
