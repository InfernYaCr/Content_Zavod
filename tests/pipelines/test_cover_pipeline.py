import base64

import pytest

from content_zavod.job_queue import JobPartialFailure
from content_zavod.pipelines.cover_pipeline import make_generate_cover_handler
from content_zavod.yandex import GeneratedImage


class FakeImageGenerator:
    def __init__(self, *, cost: float | None = None, error: Exception | None = None) -> None:
        self.prompts: list[str] = []
        self._cost = cost
        self._error = error

    async def generate_cover_with_usage(self, prompt: str) -> GeneratedImage:
        self.prompts.append(prompt)
        if self._error is not None:
            raise self._error
        return GeneratedImage(
            image=b"fake-image-bytes", model="yandex-art", latency_ms=42, cost=self._cost
        )


@pytest.mark.asyncio
async def test_handler_returns_base64_image_and_mime_type() -> None:
    image_generator = FakeImageGenerator()
    handler = make_generate_cover_handler(image_generator)

    output = await handler({"plan_item_id": "item-1", "title": "Topic A", "summary": "a summary"})

    assert output["plan_item_id"] == "item-1"
    assert base64.b64decode(output["image"]) == b"fake-image-bytes"
    assert output["mime_type"] == "image/jpeg"


@pytest.mark.asyncio
async def test_handler_builds_a_prompt_from_title_and_summary() -> None:
    image_generator = FakeImageGenerator()
    handler = make_generate_cover_handler(image_generator)

    await handler({"plan_item_id": "item-1", "title": "Topic A", "summary": "a summary"})

    assert image_generator.prompts == ["Обложка для статьи «Topic A». a summary"]


@pytest.mark.asyncio
async def test_handler_builds_a_prompt_without_summary() -> None:
    image_generator = FakeImageGenerator()
    handler = make_generate_cover_handler(image_generator)

    await handler({"plan_item_id": "item-1", "title": "Topic A", "summary": ""})

    assert image_generator.prompts == ["Обложка для статьи «Topic A»."]


@pytest.mark.asyncio
async def test_handler_reports_step_provenance_and_cost() -> None:
    image_generator = FakeImageGenerator(cost=5.0)
    handler = make_generate_cover_handler(image_generator)

    output = await handler({"plan_item_id": "item-1", "title": "Topic A", "summary": ""})

    [step] = output["steps"]
    assert step["step_name"] == "cover"
    assert step["provider"] == "yandex"
    assert step["model"] == "yandex-art"
    assert step["cost"] == 5.0
    assert step["latency_ms"] == 42


@pytest.mark.asyncio
async def test_handler_failure_raises_partial_failure_with_plan_item_id() -> None:
    image_generator = FakeImageGenerator(error=RuntimeError("model unavailable"))
    handler = make_generate_cover_handler(image_generator)

    with pytest.raises(JobPartialFailure) as excinfo:
        await handler({"plan_item_id": "item-1", "title": "Topic A", "summary": ""})

    assert excinfo.value.partial_output["plan_item_id"] == "item-1"
    assert excinfo.value.partial_output["steps"] == []
