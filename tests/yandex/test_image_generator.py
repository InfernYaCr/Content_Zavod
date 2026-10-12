import base64

import pytest

from content_zavod.yandex.credentials import StaticApiKeyProvider
from content_zavod.yandex.errors import AuthError, ContentPolicyError, RateLimited, YandexError
from content_zavod.yandex.http import HttpResponse
from content_zavod.yandex.image_generator import GENERATE_URL, PROMPT_LIMIT, ImageGenerator

from .fakes import FakeClock, FakeHttpTransport, RecordingSleep

PNG = b"\x89PNG\r\n\x1a\nfake"


def _image_response(image: bytes = PNG) -> HttpResponse:
    return HttpResponse(
        status=200,
        body={"created": 1, "data": [{"b64_json": base64.b64encode(image).decode()}]},
    )


def _make_generator(transport: FakeHttpTransport, **kwargs: object) -> ImageGenerator:
    return ImageGenerator(
        transport,
        StaticApiKeyProvider("key"),
        folder_id="folder-1",
        sleep=RecordingSleep(),
        clock=FakeClock(),
        **kwargs,
    )


@pytest.mark.asyncio
async def test_generate_cover_posts_one_synchronous_request_and_decodes_the_image() -> None:
    transport = FakeHttpTransport()
    transport.queue_post(GENERATE_URL, _image_response())
    generator = _make_generator(transport)

    result = await generator.generate_cover("a cat riding a bike")

    assert result == PNG
    assert transport.get_calls == []
    [(url, headers, body)] = transport.post_calls
    assert url == "https://ai.api.cloud.yandex.net/v1/images/generations"
    assert headers == {"Authorization": "Api-Key key", "OpenAI-Project": "folder-1"}
    assert body == {
        "model": "art://folder-1/aliceai-image-art-3.0",
        "prompt": "a cat riding a bike",
        "size": "1024x1024",
    }


@pytest.mark.asyncio
async def test_generate_cover_with_usage_reports_model_latency_cost_and_mime_type() -> None:
    transport = FakeHttpTransport()
    jpeg = b"\xff\xd8\xff\xe0fake"
    transport.queue_post(GENERATE_URL, _image_response(jpeg))
    generator = _make_generator(transport, model="some-art-model", cost_per_generation=2.23)

    generated = await generator.generate_cover_with_usage("a cat riding a bike")

    assert generated.image == jpeg
    assert generated.model == "some-art-model"
    assert generated.cost == 2.23
    assert generated.mime_type == "image/jpeg"
    assert generated.latency_ms >= 0
    assert transport.post_calls[0][2]["model"] == "art://folder-1/some-art-model"


@pytest.mark.asyncio
async def test_generate_cover_with_usage_leaves_cost_unknown_without_pricing_configured() -> None:
    transport = FakeHttpTransport()
    transport.queue_post(GENERATE_URL, _image_response())
    generator = _make_generator(transport)

    generated = await generator.generate_cover_with_usage("prompt")

    assert generated.cost is None
    assert generated.mime_type == "image/png"


@pytest.mark.asyncio
async def test_long_prompt_is_cut_at_a_word_boundary_within_the_limit() -> None:
    transport = FakeHttpTransport()
    transport.queue_post(GENERATE_URL, _image_response())
    generator = _make_generator(transport)

    await generator.generate_cover("слово " * 200)

    prompt = transport.post_calls[0][2]["prompt"]
    assert len(prompt) <= PROMPT_LIMIT
    assert prompt.endswith("слово…")


@pytest.mark.asyncio
async def test_retries_on_rate_limit_then_succeeds() -> None:
    transport = FakeHttpTransport()
    transport.queue_post(GENERATE_URL, HttpResponse(status=429, body={}))
    transport.queue_post(GENERATE_URL, _image_response())
    generator = _make_generator(transport)

    assert await generator.generate_cover("prompt") == PNG
    assert len(transport.post_calls) == 2


@pytest.mark.asyncio
async def test_raises_rate_limited_once_retries_run_out() -> None:
    transport = FakeHttpTransport()
    for _ in range(2):
        transport.queue_post(GENERATE_URL, HttpResponse(status=429, body={}))
    generator = _make_generator(transport, max_retries=1)

    with pytest.raises(RateLimited):
        await generator.generate_cover("prompt")


@pytest.mark.asyncio
async def test_403_missing_role_or_scope_is_an_auth_error_naming_the_cause() -> None:
    transport = FakeHttpTransport()
    transport.queue_post(
        GENERATE_URL,
        HttpResponse(status=403, body={"error": {"message": "Permission denied", "code": 7}}),
    )
    generator = _make_generator(transport)

    with pytest.raises(AuthError, match="Permission denied"):
        await generator.generate_cover("prompt")


@pytest.mark.asyncio
async def test_402_is_a_billing_error() -> None:
    transport = FakeHttpTransport()
    transport.queue_post(
        GENERATE_URL, HttpResponse(status=402, body={"error": {"message": "Payment required"}})
    )
    generator = _make_generator(transport)

    with pytest.raises(YandexError, match="balance"):
        await generator.generate_cover("prompt")


@pytest.mark.asyncio
async def test_400_content_filter_is_a_content_policy_error() -> None:
    transport = FakeHttpTransport()
    transport.queue_post(
        GENERATE_URL,
        HttpResponse(status=400, body={"error": {"message": "Request blocked by content filter"}}),
    )
    generator = _make_generator(transport)

    with pytest.raises(ContentPolicyError):
        await generator.generate_cover("bad prompt")


@pytest.mark.asyncio
async def test_raises_on_a_response_without_an_image() -> None:
    transport = FakeHttpTransport()
    transport.queue_post(GENERATE_URL, HttpResponse(status=200, body={"data": []}))
    generator = _make_generator(transport)

    with pytest.raises(YandexError, match="Malformed"):
        await generator.generate_cover("prompt")
