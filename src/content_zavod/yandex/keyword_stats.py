"""KeywordStats: a narrow client to Wordstat via Yandex Search API.

Invariant (per issue #3): `keyword_stats()` never raises when Wordstat is
unavailable. A failure for one keyword just means that keyword is missing
from the returned dict, so a Plan can still be generated without its
numbers. Caches frequencies internally so repeated lookups don't re-hit
the API.

`searchapi.api.cloud.yandex.net` was observed (see
`docs/integrations/yandex-search-api.md`) to occasionally fail with
SSL/connect errors on the first attempt and to take up to 60-90s to
respond, hence the connection retry and generous default timeout.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from ._resilience import with_connection_retry
from .credentials import CredentialProvider, IamTokenProvider, StaticApiKeyProvider
from .errors import YandexError
from .http import HttpResponse, HttpTransport, HttpxTransport

TOP_REQUESTS_URL = "https://searchapi.api.cloud.yandex.net/v2/wordstat/topRequests"
DYNAMICS_URL = "https://searchapi.api.cloud.yandex.net/v2/wordstat/dynamics"

DEFAULT_TIMEOUT = 90.0

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class KeywordStat:
    keyword: str
    frequency: int


@dataclass(frozen=True)
class KeywordDynamicsPoint:
    date: str
    count: int
    share: float


class KeywordStats:
    def __init__(
        self,
        transport: HttpTransport,
        credentials: CredentialProvider,
        *,
        folder_id: str,
        cache_ttl: float = 3600.0,
        max_retries: int = 2,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
        top_requests_url: str = TOP_REQUESTS_URL,
        dynamics_url: str = DYNAMICS_URL,
    ) -> None:
        self._transport = transport
        self._credentials = credentials
        self._folder_id = folder_id
        self._cache_ttl = cache_ttl
        self._max_retries = max_retries
        self._sleep = sleep
        self._clock = clock
        self._top_requests_url = top_requests_url
        self._dynamics_url = dynamics_url
        self._cache: dict[str, tuple[float, KeywordStat]] = {}

    @classmethod
    def with_service_account_key(
        cls, api_key: str, *, folder_id: str, **kwargs: Any
    ) -> KeywordStats:
        """Build against a Yandex Cloud service-account API key (no expiry to manage)."""
        return cls(
            HttpxTransport(timeout=DEFAULT_TIMEOUT),
            StaticApiKeyProvider(api_key),
            folder_id=folder_id,
            **kwargs,
        )

    @classmethod
    def with_oauth_token(cls, oauth_token: str, *, folder_id: str, **kwargs: Any) -> KeywordStats:
        """Build against a long-lived OAuth token; IAM tokens are refreshed internally."""
        transport = HttpxTransport(timeout=DEFAULT_TIMEOUT)
        return cls(
            transport,
            IamTokenProvider(transport, oauth_token=oauth_token),
            folder_id=folder_id,
            **kwargs,
        )

    async def keyword_stats(self, keywords: list[str]) -> dict[str, KeywordStat]:
        result: dict[str, KeywordStat] = {}
        for keyword in keywords:
            cached = self._cached(keyword)
            if cached is not None:
                result[keyword] = cached
                continue
            try:
                stat = await self._fetch(keyword)
            except Exception:
                logger.warning("Wordstat lookup failed for keyword %r", keyword, exc_info=True)
                continue
            self._cache[keyword] = (self._clock(), stat)
            result[keyword] = stat
        return result

    async def keyword_dynamics(
        self,
        keyword: str,
        *,
        period: str,
        from_date: str,
        to_date: str,
    ) -> list[KeywordDynamicsPoint]:
        headers = await self._credentials.auth_header()

        async def call() -> HttpResponse:
            return await self._transport.post(
                self._dynamics_url,
                headers=headers,
                json={
                    "phrase": keyword,
                    "period": period,
                    "fromDate": from_date,
                    "toDate": to_date,
                    "folderId": self._folder_id,
                },
            )

        response = await with_connection_retry(
            call, max_retries=self._max_retries, sleep=self._sleep
        )
        if response.status != 200:
            raise YandexError(
                f"Wordstat dynamics request failed (status={response.status}): {response.body}"
            )
        try:
            return [
                KeywordDynamicsPoint(
                    date=str(point["date"]),
                    count=int(point["count"]),
                    share=float(point["share"]),
                )
                for point in response.body["results"]
            ]
        except (KeyError, TypeError, ValueError) as exc:
            raise YandexError(f"Malformed Wordstat dynamics response: {response.body}") from exc

    def _cached(self, keyword: str) -> KeywordStat | None:
        entry = self._cache.get(keyword)
        if entry is None:
            return None
        cached_at, stat = entry
        if self._clock() - cached_at > self._cache_ttl:
            return None
        return stat

    async def _fetch(self, keyword: str) -> KeywordStat:
        headers = await self._credentials.auth_header()

        async def call() -> HttpResponse:
            return await self._transport.post(
                self._top_requests_url,
                headers=headers,
                json={"phrase": keyword, "numPhrases": 20, "folderId": self._folder_id},
            )

        response = await with_connection_retry(
            call, max_retries=self._max_retries, sleep=self._sleep
        )
        if response.status != 200:
            raise YandexError(f"Wordstat request failed (status={response.status})")
        try:
            frequency = int(response.body["results"][0]["count"])
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise YandexError(f"Malformed Wordstat response: {response.body}") from exc
        return KeywordStat(keyword=keyword, frequency=frequency)
