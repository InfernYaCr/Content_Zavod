import base64

import pytest

from content_zavod.yandex.credentials import StaticApiKeyProvider
from content_zavod.yandex.errors import YandexError
from content_zavod.yandex.http import HttpResponse
from content_zavod.yandex.image_generator import GENERATE_URL, ImageGenerator

from .fakes import FakeClock, FakeHttpTransport, RecordingSleep

OPERATION_URL = "https://operation.api.cloud.yandex.net/operations/op-1"


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
async def test_generate_cover_submits_polls_and_decodes_result() -> None:
    transport = FakeHttpTransport()
    image_bytes = b"fake-png-bytes"
    transport.queue_post(GENERATE_URL, HttpResponse(status=200, body={"id": "op-1"}))
    transport.queue_get(OPERATION_URL, HttpResponse(status=200, body={"done": False}))
    transport.queue_get(
        OPERATION_URL,
        HttpResponse(
            status=200,
            body={"done": True, "response": {"image": base64.b64encode(image_bytes).decode()}},
        ),
    )
    generator = _make_generator(transport)

    result = await generator.generate_cover("a cat riding a bike")

    assert result == image_bytes
    assert len(transport.get_calls) == 2
    _, _, submit_body = transport.post_calls[0]
    assert submit_body["messages"] == [{"weight": 1, "text": "a cat riding a bike"}]


@pytest.mark.asyncio
async def test_generate_cover_with_usage_reports_model_latency_and_cost() -> None:
    transport = FakeHttpTransport()
    image_bytes = b"fake-png-bytes"
    transport.queue_post(GENERATE_URL, HttpResponse(status=200, body={"id": "op-1"}))
    transport.queue_get(
        OPERATION_URL,
        HttpResponse(
            status=200,
            body={"done": True, "response": {"image": base64.b64encode(image_bytes).decode()}},
        ),
    )
    generator = _make_generator(transport, model="yandex-art", cost_per_generation=5.0)

    generated = await generator.generate_cover_with_usage("a cat riding a bike")

    assert generated.image == image_bytes
    assert generated.model == "yandex-art"
    assert generated.cost == 5.0
    assert generated.latency_ms >= 0


@pytest.mark.asyncio
async def test_generate_cover_with_usage_leaves_cost_unknown_without_pricing_configured() -> None:
    transport = FakeHttpTransport()
    transport.queue_post(GENERATE_URL, HttpResponse(status=200, body={"id": "op-1"}))
    transport.queue_get(
        OPERATION_URL,
        HttpResponse(
            status=200,
            body={"done": True, "response": {"image": base64.b64encode(b"x").decode()}},
        ),
    )
    generator = _make_generator(transport)

    generated = await generator.generate_cover_with_usage("prompt")

    assert generated.cost is None


@pytest.mark.asyncio
async def test_raises_on_operation_error() -> None:
    transport = FakeHttpTransport()
    transport.queue_post(GENERATE_URL, HttpResponse(status=200, body={"id": "op-1"}))
    transport.queue_get(
        OPERATION_URL,
        HttpResponse(status=200, body={"done": True, "error": {"message": "unsafe prompt"}}),
    )
    generator = _make_generator(transport)

    with pytest.raises(YandexError):
        await generator.generate_cover("bad prompt")


@pytest.mark.asyncio
async def test_raises_on_poll_timeout() -> None:
    transport = FakeHttpTransport()
    clock = FakeClock()
    transport.queue_post(GENERATE_URL, HttpResponse(status=200, body={"id": "op-1"}))

    class AdvancingSleep:
        def __init__(self, clock: FakeClock) -> None:
            self._clock = clock

        async def __call__(self, delay: float) -> None:
            self._clock.advance(delay)
            transport.queue_get(OPERATION_URL, HttpResponse(status=200, body={"done": False}))

    transport.queue_get(OPERATION_URL, HttpResponse(status=200, body={"done": False}))
    generator = ImageGenerator(
        transport,
        StaticApiKeyProvider("key"),
        folder_id="folder-1",
        clock=clock,
        sleep=AdvancingSleep(clock),
        poll_timeout=5.0,
        poll_interval=2.0,
    )

    with pytest.raises(YandexError):
        await generator.generate_cover("prompt")
