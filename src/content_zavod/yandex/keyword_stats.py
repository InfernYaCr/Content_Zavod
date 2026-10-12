"""KeywordStats: a narrow client to Wordstat via Yandex Search API.

Two calls, both raising `YandexError` on failure - the caller (the Карта Ниши and the weekly
Plan, #128) decides that a missing number means "no signal", never a failed Plan:
- `top_requests` (`GetTop`): the phrase's monthly frequency, the most frequent queries that
  contain it and Yandex's associations (similar queries). Counts arrive as strings.
- `keyword_dynamics` (`GetDynamics`): the count per week or month.

Wordstat's quota is 100 requests an hour and 10 a second per folder. A 429 is waited out and
retried a couple of times with its own delay (`rate_limit_delay`, longer than the connection
retry's), then raised as `RateLimited` for the caller to stop asking.

`searchapi.api.cloud.yandex.net` was observed (see
`docs/integrations/yandex-search-api.md`) to occasionally fail with
SSL/connect errors on the first attempt and to take up to 60-90s to
respond, hence the connection retry and generous default timeout.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from ._resilience import with_backoff_retry, with_connection_retry
from .credentials import CredentialProvider, IamTokenProvider, StaticApiKeyProvider
from .errors import YandexError
from .http import HttpResponse, HttpTransport, HttpxTransport

TOP_REQUESTS_URL = "https://searchapi.api.cloud.yandex.net/v2/wordstat/topRequests"
DYNAMICS_URL = "https://searchapi.api.cloud.yandex.net/v2/wordstat/dynamics"

DEFAULT_TIMEOUT = 90.0
RATE_LIMIT_DELAY = 2.0
"""First wait after a Wordstat 429, doubled per retry: the response names no wait of its own."""


@dataclass(frozen=True)
class KeywordStat:
    keyword: str
    frequency: int


@dataclass(frozen=True)
class TopRequests:
    """`GetTop` for one phrase: `frequency` is the phrase's own monthly count (`totalCount`),
    `results` the most frequent queries containing it, `associations` similar queries."""

    frequency: int
    results: list[KeywordStat]
    associations: list[KeywordStat]


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
        max_retries: int = 2,
        rate_limit_retries: int = 2,
        rate_limit_delay: float = RATE_LIMIT_DELAY,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        top_requests_url: str = TOP_REQUESTS_URL,
        dynamics_url: str = DYNAMICS_URL,
    ) -> None:
        self._transport = transport
        self._credentials = credentials
        self._folder_id = folder_id
        self._max_retries = max_retries
        self._rate_limit_retries = rate_limit_retries
        self._rate_limit_delay = rate_limit_delay
        self._sleep = sleep
        self._top_requests_url = top_requests_url
        self._dynamics_url = dynamics_url

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

    async def top_requests(self, phrase: str, *, num_phrases: int = 20) -> TopRequests:
        response = await self._post(
            self._top_requests_url, {"phrase": phrase, "numPhrases": num_phrases}
        )
        try:
            body = response.body
            results = _stats(body.get("results") or [])
            total = body.get("totalCount")
            return TopRequests(
                frequency=int(total)
                if total is not None
                else sum(r.frequency for r in results[:1]),
                results=results,
                associations=_stats(body.get("associations") or []),
            )
        except (AttributeError, KeyError, TypeError, ValueError) as exc:
            raise YandexError(f"Malformed Wordstat response: {response.body}") from exc

    async def keyword_dynamics(
        self,
        keyword: str,
        *,
        period: str,
        from_date: str,
        to_date: str,
    ) -> list[KeywordDynamicsPoint]:
        response = await self._post(
            self._dynamics_url,
            {"phrase": keyword, "period": period, "fromDate": from_date, "toDate": to_date},
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

    async def _post(self, url: str, body: dict[str, Any]) -> HttpResponse:
        """The 200 response, or the mapped error - `RateLimited` once a 429 outlasts the retries."""
        headers = await self._credentials.auth_header()

        async def call() -> HttpResponse:
            return await self._transport.post(
                url, headers=headers, json={**body, "folderId": self._folder_id}
            )

        async def attempt() -> HttpResponse:
            return await with_connection_retry(
                call, max_retries=self._max_retries, sleep=self._sleep
            )

        return await with_backoff_retry(
            attempt,
            max_retries=self._rate_limit_retries,
            sleep=self._sleep,
            base_delay=self._rate_limit_delay,
        )


def _stats(items: list[dict[str, Any]]) -> list[KeywordStat]:
    return [
        KeywordStat(keyword=str(item["phrase"]), frequency=int(item["count"])) for item in items
    ]
