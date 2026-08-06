"""Error types raised by the job queue."""

from __future__ import annotations


class JobQueueError(Exception):
    """Base class for errors raised by the job queue."""


class JobNotFound(JobQueueError):
    """No job exists with the given id."""

    def __init__(self, job_id: object) -> None:
        super().__init__(f"No job with id={job_id!r}")
