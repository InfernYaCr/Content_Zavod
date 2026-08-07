import base64

import pytest

from content_zavod.pipelines.cover_pipeline import make_generate_cover_handler


class FakeImageGenerator:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    async def generate_cover(self, prompt: str) -> bytes:
        self.prompts.append(prompt)
        return b"fake-image-bytes"


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
