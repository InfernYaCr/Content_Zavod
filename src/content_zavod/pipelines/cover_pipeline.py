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

_MIME_TYPE = "image/jpeg"


def make_generate_cover_handler(image_generator: ImageGenerator) -> JobHandler:
    async def handle(payload: dict[str, Any]) -> dict[str, Any]:
        title = payload["title"]
        summary = payload.get("summary") or ""
        image = await image_generator.generate_cover(_build_prompt(title, summary))
        return {
            "plan_item_id": payload["plan_item_id"],
            "image": base64.b64encode(image).decode("ascii"),
            "mime_type": _MIME_TYPE,
        }

    return handle


def _build_prompt(title: str, summary: str) -> str:
    if summary:
        return f"Обложка для статьи «{title}». {summary}"
    return f"Обложка для статьи «{title}»."
