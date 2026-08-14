"""generate_cover Job Handler: one YandexART cover image per Тема.

Follows the same shape as every other pipeline handler here: it computes a
JSON-serializable output and never touches Postgres directly. The
notification dispatcher persists the result via `Plan.apply_cover`.
"""

from __future__ import annotations

import base64
from typing import Any

from ..job_queue import JobHandler
from ..yandex import ImageGenerator
from .provenance import StepRecord, StepRecorder, prompt_hash

_MIME_TYPE = "image/jpeg"

# Bumped whenever `_build_prompt` changes shape (#74).
_COVER_PROMPT_VERSION = "cover-v1"


def make_generate_cover_handler(image_generator: ImageGenerator) -> JobHandler:
    async def handle(payload: dict[str, Any]) -> dict[str, Any]:
        title = payload["title"]
        summary = payload.get("summary") or ""
        plan_item_id = payload["plan_item_id"]
        prompt = _build_prompt(title, summary)
        recorder = StepRecorder()
        try:
            generated = await image_generator.generate_cover_with_usage(prompt)
        except Exception as exc:
            raise recorder.fail(exc, plan_item_id=plan_item_id) from exc
        recorder.add(
            StepRecord(
                step_name="cover",
                provider="yandex",
                model=generated.model,
                params={},
                prompt_template_version=_COVER_PROMPT_VERSION,
                prompt_hash=prompt_hash(prompt),
                tokens=None,
                usage_missing=False,
                latency_ms=generated.latency_ms,
                cost=generated.cost,
            )
        )
        return {
            "plan_item_id": plan_item_id,
            "image": base64.b64encode(generated.image).decode("ascii"),
            "mime_type": _MIME_TYPE,
            "steps": recorder.as_output(),
        }

    return handle


def _build_prompt(title: str, summary: str) -> str:
    if summary:
        return f"Обложка для статьи «{title}». {summary}"
    return f"Обложка для статьи «{title}»."
