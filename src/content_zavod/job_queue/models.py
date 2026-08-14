"""Types shared across the job queue: identifiers, status, handler contract, and results."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal

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


class JobPartialFailure(Exception):
    """Raised by a Job Handler that wants a failure to still carry whatever
    JSON-serializable partial output it produced before failing - e.g. the
    provenance/cost of LLM steps that ran successfully before a later step
    errored out. `run_worker`'s `on_attempt` hook receives `partial_output`
    for every attempt, not only the one a Job finally succeeds or exhausts
    retries on, so a Job's total cost reflects every attempt (#74)."""

    def __init__(self, message: str, partial_output: dict[str, Any]) -> None:
        super().__init__(message)
        self.partial_output = partial_output
