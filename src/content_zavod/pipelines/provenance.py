"""Shared per-step provenance recording for every Job Handler that calls a
Yandex model (#74). Each LLM/image call becomes one `StepRecord` - provider,
model, params, prompt template version + hash, tokens, latency, and cost -
appended to a `StepRecorder` as the handler runs. `StepRecorder.fail` turns
the underlying exception into a `JobPartialFailure` carrying every step
completed before the failure, so a Job's cost still reflects attempts that
error out partway through instead of losing that spend when the attempt as a
whole fails.

`cost`/`tokens` stay `None` (never silently 0) when the provider didn't
report usage or pricing isn't configured - `usage_missing` says which.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

from ..job_queue import JobPartialFailure
from ..yandex import Completion

_YANDEX_PROVIDER = "yandex"


def prompt_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class StepRecord:
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

    @classmethod
    def from_completion(
        cls,
        completion: Completion,
        *,
        step_name: str,
        prompt_template_version: str,
        prompt_text: str,
        params: dict[str, Any],
    ) -> StepRecord:
        """Builds the record every text-generation step shares - only what varies per call
        (step name, prompt version/text, request params) needs to be passed in."""
        return cls(
            step_name=step_name,
            provider=_YANDEX_PROVIDER,
            model=completion.model,
            params=params,
            prompt_template_version=prompt_template_version,
            prompt_hash=prompt_hash(prompt_text),
            tokens=completion.tokens,
            usage_missing=completion.usage_missing,
            latency_ms=completion.latency_ms,
            cost=completion.cost,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_name": self.step_name,
            "provider": self.provider,
            "model": self.model,
            "params": self.params,
            "prompt_template_version": self.prompt_template_version,
            "prompt_hash": self.prompt_hash,
            "tokens": self.tokens,
            "usage_missing": self.usage_missing,
            "latency_ms": self.latency_ms,
            "cost": self.cost,
        }


@dataclass
class StepRecorder:
    steps: list[StepRecord] = field(default_factory=list)

    def add(self, step: StepRecord) -> None:
        self.steps.append(step)

    def as_output(self) -> list[dict[str, Any]]:
        return [step.to_dict() for step in self.steps]

    def fail(self, exc: Exception, **extra: Any) -> JobPartialFailure:
        return JobPartialFailure(str(exc), {"steps": self.as_output(), **extra})
