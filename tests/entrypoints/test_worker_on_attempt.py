"""Unit tests for worker_main's `_make_on_attempt` glue (#74): the one piece
of non-trivial logic that decides which steps get persisted after an attempt
settles - everything else in entrypoints/worker.py is Postgres/Yandex
wiring, verified by local manual runs.
"""

from __future__ import annotations

from content_zavod.entrypoints.worker import _make_on_attempt
from content_zavod.job_queue import ClaimedJob, JobPartialFailure

_CLAIMED = ClaimedJob(id=1, job_type="generate_article", payload={}, attempts=1, lease_token="t")


class FakeGenerationSteps:
    def __init__(self) -> None:
        self.recorded: list[tuple[int, str, str | None, list[dict]]] = []

    async def record_many(
        self, *, job_id: int, job_type: str, article_id: str | None, steps: list[dict]
    ) -> None:
        self.recorded.append((job_id, job_type, article_id, steps))


async def test_records_steps_from_successful_output() -> None:
    generation_steps = FakeGenerationSteps()
    on_attempt = _make_on_attempt(generation_steps)

    await on_attempt(_CLAIMED, {"article_id": "a-1", "steps": [{"step_name": "outline"}]}, None)

    assert generation_steps.recorded == [(1, "generate_article", "a-1", [{"step_name": "outline"}])]


async def test_records_steps_from_a_job_partial_failure() -> None:
    generation_steps = FakeGenerationSteps()
    on_attempt = _make_on_attempt(generation_steps)
    error = JobPartialFailure("boom", {"article_id": "a-1", "steps": [{"step_name": "outline"}]})

    await on_attempt(_CLAIMED, None, error)

    assert generation_steps.recorded == [(1, "generate_article", "a-1", [{"step_name": "outline"}])]


async def test_does_not_record_on_a_plain_exception_without_partial_output() -> None:
    generation_steps = FakeGenerationSteps()
    on_attempt = _make_on_attempt(generation_steps)

    await on_attempt(_CLAIMED, None, RuntimeError("boom"))

    assert generation_steps.recorded == []


async def test_does_not_record_when_output_has_no_steps() -> None:
    generation_steps = FakeGenerationSteps()
    on_attempt = _make_on_attempt(generation_steps)

    await on_attempt(_CLAIMED, {"week_label": "Week 1"}, None)

    assert generation_steps.recorded == []
