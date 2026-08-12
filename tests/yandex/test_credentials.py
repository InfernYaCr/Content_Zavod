import datetime as dt

import pytest

from content_zavod.yandex.credentials import IamTokenProvider, StaticApiKeyProvider
from content_zavod.yandex.errors import AuthError
from content_zavod.yandex.http import HttpResponse

from .fakes import FakeClock, FakeHttpTransport

IAM_URL = "https://iam.api.cloud.yandex.net/iam/v1/tokens"


def _iso_at(epoch: float) -> str:
    return dt.datetime.fromtimestamp(epoch, tz=dt.UTC).isoformat()


def _queue_token(transport: FakeHttpTransport, *, value: str, expires_at: float) -> None:
    transport.queue_post(
        IAM_URL,
        HttpResponse(status=200, body={"iamToken": value, "expiresAt": _iso_at(expires_at)}),
    )


@pytest.mark.asyncio
async def test_issues_and_caches_token() -> None:
    transport = FakeHttpTransport()
    clock = FakeClock(start=1_700_000_000.0)
    _queue_token(transport, value="token-1", expires_at=clock.now + 3600)
    provider = IamTokenProvider(transport, oauth_token="oauth", clock=clock, refresh_margin=60)

    first = await provider.auth_header()
    second = await provider.auth_header()

    assert first == {"Authorization": "Bearer token-1"}
    assert second == {"Authorization": "Bearer token-1"}
    assert len(transport.post_calls) == 1


@pytest.mark.asyncio
async def test_refreshes_shortly_before_expiry() -> None:
    transport = FakeHttpTransport()
    clock = FakeClock(start=1_700_000_000.0)
    _queue_token(transport, value="token-1", expires_at=clock.now + 3600)
    _queue_token(transport, value="token-2", expires_at=clock.now + 3600 + 7200)
    provider = IamTokenProvider(transport, oauth_token="oauth", clock=clock, refresh_margin=60)

    await provider.auth_header()
    clock.advance(3600 - 30)  # inside the refresh margin
    second = await provider.auth_header()

    assert second == {"Authorization": "Bearer token-2"}
    assert len(transport.post_calls) == 2


@pytest.mark.asyncio
async def test_raises_auth_error_on_failed_issuance() -> None:
    transport = FakeHttpTransport()
    transport.queue_post(IAM_URL, HttpResponse(status=401, body={}))
    provider = IamTokenProvider(transport, oauth_token="bad-oauth")

    with pytest.raises(AuthError):
        await provider.auth_header()


@pytest.mark.asyncio
async def test_static_api_key_provider_never_calls_transport() -> None:
    provider = StaticApiKeyProvider("service-key")

    header = await provider.auth_header()

    assert header == {"Authorization": "Api-Key service-key"}
