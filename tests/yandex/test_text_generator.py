import pytest

from content_zavod.yandex.credentials import StaticApiKeyProvider
from content_zavod.yandex.errors import AuthError, ContentPolicyError, RateLimited
from content_zavod.yandex.http import HttpResponse
from content_zavod.yandex.text_generator import COMPLETION_URL, Message, TextGenerator

from .fakes import FakeHttpTransport, RecordingSleep


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
