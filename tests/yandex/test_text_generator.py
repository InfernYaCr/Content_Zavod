import pytest

from content_zavod.yandex.credentials import StaticApiKeyProvider
from content_zavod.yandex.errors import AuthError, ContentPolicyError, RateLimited
from content_zavod.yandex.http import HttpResponse
from content_zavod.yandex.text_generator import COMPLETION_URL, Message, TextGenerator

from .fakes import FakeClock, FakeHttpTransport, RecordingSleep


def _success_response(text: str) -> HttpResponse:
    return HttpResponse(
        status=200,
        body={"result": {"alternatives": [{"message": {"role": "assistant", "text": text}}]}},
    )


def _make_generator(transport: FakeHttpTransport, **kwargs: object) -> TextGenerator:
    return TextGenerator(
        transport,
        StaticApiKeyProvider("key"),
        folder_id="folder-1",
        sleep=RecordingSleep(),
        **kwargs,
    )


@pytest.mark.asyncio
async def test_complete_returns_text_on_success() -> None:
    transport = FakeHttpTransport()
    transport.queue_post(COMPLETION_URL, _success_response("hello there"))
    generator = _make_generator(transport)

    result = await generator.complete([Message(role="user", text="hi")])

    assert result == "hello there"
    assert len(transport.post_calls) == 1
    _, headers, body = transport.post_calls[0]
    assert headers == {"Authorization": "Api-Key key"}
    assert body["messages"] == [{"role": "user", "text": "hi"}]
    assert body["completionOptions"]["temperature"] == 0.7


@pytest.mark.asyncio
async def test_retries_on_rate_limit_then_succeeds() -> None:
    transport = FakeHttpTransport()
    transport.queue_post(COMPLETION_URL, HttpResponse(status=429, body={}))
    transport.queue_post(COMPLETION_URL, _success_response("after retry"))
    generator = _make_generator(transport, max_retries=3)

    result = await generator.complete([Message(role="user", text="hi")])

    assert result == "after retry"
    assert len(transport.post_calls) == 2


@pytest.mark.asyncio
async def test_raises_rate_limited_after_exhausting_retries() -> None:
    transport = FakeHttpTransport()
    for _ in range(3):
        transport.queue_post(COMPLETION_URL, HttpResponse(status=429, body={}))
    generator = _make_generator(transport, max_retries=2)

    with pytest.raises(RateLimited):
        await generator.complete([Message(role="user", text="hi")])

    assert len(transport.post_calls) == 3


@pytest.mark.asyncio
async def test_maps_403_to_auth_error() -> None:
    transport = FakeHttpTransport()
    transport.queue_post(COMPLETION_URL, HttpResponse(status=403, body={}))
    generator = _make_generator(transport)

    with pytest.raises(AuthError):
        await generator.complete([Message(role="user", text="hi")])


@pytest.mark.asyncio
async def test_maps_content_policy_violation() -> None:
    transport = FakeHttpTransport()
    transport.queue_post(
        COMPLETION_URL,
        HttpResponse(status=400, body={"message": "request blocked by content filter"}),
    )
    generator = _make_generator(transport)

    with pytest.raises(ContentPolicyError):
        await generator.complete([Message(role="user", text="hi")])


def test_with_service_account_key_does_not_require_credential_wiring() -> None:
    generator = TextGenerator.with_service_account_key("service-key", folder_id="folder-1")

    assert isinstance(generator, TextGenerator)


@pytest.mark.asyncio
async def test_complete_with_usage_returns_text_model_and_tokens() -> None:
    transport = FakeHttpTransport()
    transport.queue_post(
        COMPLETION_URL,
        HttpResponse(
            status=200,
            body={
                "result": {
                    "alternatives": [{"message": {"role": "assistant", "text": "hello there"}}],
                    "usage": {"totalTokens": "42"},
                    "modelVersion": "yandexgpt/latest",
                }
            },
        ),
    )
    generator = _make_generator(transport)

    completion = await generator.complete_with_usage([Message(role="user", text="hi")])

    assert completion.text == "hello there"
    assert completion.tokens == 42
    assert completion.model == "yandexgpt/latest"


@pytest.mark.asyncio
async def test_complete_with_usage_defaults_tokens_and_model_when_absent() -> None:
    transport = FakeHttpTransport()
    transport.queue_post(COMPLETION_URL, _success_response("no usage field"))
    generator = _make_generator(transport)

    completion = await generator.complete_with_usage([Message(role="user", text="hi")])

    assert completion.text == "no usage field"
    assert completion.tokens == 0
    assert completion.model == ""
    assert completion.usage_missing is True
    assert completion.cost is None


@pytest.mark.asyncio
async def test_complete_with_usage_flags_usage_present_when_reported() -> None:
    transport = FakeHttpTransport()
    transport.queue_post(
        COMPLETION_URL,
        HttpResponse(
            status=200,
            body={
                "result": {
                    "alternatives": [{"message": {"role": "assistant", "text": "hello there"}}],
                    "usage": {"totalTokens": "42"},
                    "modelVersion": "yandexgpt/latest",
                }
            },
        ),
    )
    generator = _make_generator(transport)

    completion = await generator.complete_with_usage([Message(role="user", text="hi")])

    assert completion.usage_missing is False


class _ClockAdvancingTransport:
    """Wraps `FakeHttpTransport`, advancing a `FakeClock` by a fixed amount on every
    POST - simulates a call that takes real wall time, for asserting latency."""

    def __init__(self, inner: FakeHttpTransport, clock: FakeClock, *, seconds: float) -> None:
        self._inner = inner
        self._clock = clock
        self._seconds = seconds

    async def post(self, *args: object, **kwargs: object) -> HttpResponse:
        self._clock.advance(self._seconds)
        return await self._inner.post(*args, **kwargs)  # type: ignore[arg-type]

    async def get(self, *args: object, **kwargs: object) -> HttpResponse:
        return await self._inner.get(*args, **kwargs)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_complete_with_usage_records_latency() -> None:
    transport = FakeHttpTransport()
    transport.queue_post(COMPLETION_URL, _success_response("hi"))
    clock = FakeClock()
    generator = TextGenerator(
        _ClockAdvancingTransport(transport, clock, seconds=0.25),
        StaticApiKeyProvider("key"),
        folder_id="folder-1",
        sleep=RecordingSleep(),
        clock=clock,
    )

    completion = await generator.complete_with_usage([Message(role="user", text="hi")])

    assert completion.latency_ms == 250


@pytest.mark.asyncio
async def test_complete_with_usage_computes_cost_from_configured_pricing() -> None:
    transport = FakeHttpTransport()
    transport.queue_post(
        COMPLETION_URL,
        HttpResponse(
            status=200,
            body={
                "result": {
                    "alternatives": [{"message": {"role": "assistant", "text": "hi"}}],
                    "usage": {"totalTokens": "1000"},
                }
            },
        ),
    )
    generator = _make_generator(transport, cost_per_1k_tokens=0.5)

    completion = await generator.complete_with_usage([Message(role="user", text="hi")])

    assert completion.cost == 0.5


@pytest.mark.asyncio
async def test_complete_with_usage_leaves_cost_unknown_without_pricing_configured() -> None:
    transport = FakeHttpTransport()
    transport.queue_post(
        COMPLETION_URL,
        HttpResponse(
            status=200,
            body={
                "result": {
                    "alternatives": [{"message": {"role": "assistant", "text": "hi"}}],
                    "usage": {"totalTokens": "1000"},
                }
            },
        ),
    )
    generator = _make_generator(transport)

    completion = await generator.complete_with_usage([Message(role="user", text="hi")])

    assert completion.cost is None


@pytest.mark.asyncio
async def test_complete_with_usage_leaves_cost_unknown_when_usage_is_missing() -> None:
    transport = FakeHttpTransport()
    transport.queue_post(COMPLETION_URL, _success_response("no usage field"))
    generator = _make_generator(transport, cost_per_1k_tokens=0.5)

    completion = await generator.complete_with_usage([Message(role="user", text="hi")])

    assert completion.cost is None
