"""Credential refresh: the private seam shared by TextGenerator and
ImageGenerator (per issue #3, this must not leak into either client's
public interface).

An IAM token lives 12 hours, so a client that grabs one and holds onto it
will eventually start failing with AuthError. Two strategies avoid that:

- `IamTokenProvider`: refreshes the IAM token on demand, shortly before it
  would expire, from a long-lived OAuth token.
- `StaticApiKeyProvider`: wraps a Yandex Cloud service-account API key,
  which does not expire, so there is nothing to refresh.

Both satisfy `CredentialProvider`, so TextGenerator/ImageGenerator depend on
the protocol and don't care which strategy backs it.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Protocol

from .errors import AuthError
from .http import HttpTransport

IAM_TOKEN_URL = "https://iam.api.cloud.yandex.net/iam/v1/tokens"


class CredentialProvider(Protocol):
    async def auth_header(self) -> dict[str, str]: ...


@dataclass(frozen=True)
class _CachedToken:
    value: str
    expires_at: float  # epoch seconds


class IamTokenProvider:
    def __init__(
        self,
        transport: HttpTransport,
        *,
        oauth_token: str,
        refresh_margin: float = 60.0,
        clock: Callable[[], float] = time.time,
        url: str = IAM_TOKEN_URL,
    ) -> None:
        self._transport = transport
        self._oauth_token = oauth_token
        self._refresh_margin = refresh_margin
        self._clock = clock
        self._url = url
        self._cached: _CachedToken | None = None
        self._lock = asyncio.Lock()

    async def auth_header(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {await self._get_token()}"}

    async def _get_token(self) -> str:
        async with self._lock:
            if self._needs_refresh():
                self._cached = await self._issue_token()
            assert self._cached is not None
            return self._cached.value

    def _needs_refresh(self) -> bool:
        return (
            self._cached is None
            or self._clock() >= self._cached.expires_at - self._refresh_margin
        )

    async def _issue_token(self) -> _CachedToken:
        response = await self._transport.post(
            self._url,
            headers={},
            json={"yandexPassportOauthToken": self._oauth_token},
        )
        if response.status != 200:
            raise AuthError(f"Failed to obtain IAM token (status={response.status})")
        try:
            token = response.body["iamToken"]
            expires_at = _parse_expires_at(response.body["expiresAt"])
        except (KeyError, ValueError) as exc:
            raise AuthError("Malformed IAM token response") from exc
        return _CachedToken(value=token, expires_at=expires_at)


class StaticApiKeyProvider:
    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    async def auth_header(self) -> dict[str, str]:
        return {"Authorization": f"Api-Key {self._api_key}"}


def _parse_expires_at(raw: str) -> float:
    return datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
