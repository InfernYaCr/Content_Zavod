"""WebSearch: a narrow client to Yandex Search API's web search (`POST /v2/web/search`),
the search provider behind a Тема's Исследование (#94).

Same Yandex Cloud host and credentials as Wordstat (`KeywordStats`), see
`docs/integrations/yandex-search-api.md`. The response is `{"rawData": <base64>}` holding
Yandex's XML results page (`FORMAT_XML`); only `url`/`title`/`passages` of each document
are read. Unlike `KeywordStats`, failures do raise - the caller (research step) decides
that a failed search means "no evidence" for this Тема, which has to be visible in the
Статья rather than silently swallowed here.

`searchapi.api.cloud.yandex.net` is flaky on first connection and slow (up to 60-90s),
hence the connection retry and the generous default timeout.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import time
import xml.etree.ElementTree as ET
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from ._resilience import map_error, with_connection_retry
from .credentials import CredentialProvider, IamTokenProvider, StaticApiKeyProvider
from .errors import YandexError
from .http import HttpResponse, HttpTransport, HttpxTransport

WEB_SEARCH_URL = "https://searchapi.api.cloud.yandex.net/v2/web/search"

DEFAULT_TIMEOUT = 90.0

# Yandex XML error code for "nothing found" - an empty result, not a failure.
_NOTHING_FOUND_CODE = "15"


@dataclass(frozen=True)
class SearchHit:
    url: str
    title: str
    snippet: str


@dataclass(frozen=True)
class SearchResults:
    hits: list[SearchHit]
    latency_ms: int
    # `None` when no per-request price is configured - unknown, never a silent 0 (#74).
    cost: float | None


class WebSearch:
    def __init__(
        self,
        transport: HttpTransport,
        credentials: CredentialProvider,
        *,
        folder_id: str,
        max_retries: int = 2,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
        cost_per_request: float | None = None,
        url: str = WEB_SEARCH_URL,
    ) -> None:
        self._transport = transport
        self._credentials = credentials
        self._folder_id = folder_id
        self._max_retries = max_retries
        self._sleep = sleep
        self._clock = clock
        self._cost_per_request = cost_per_request
        self._url = url

    @classmethod
    def with_service_account_key(cls, api_key: str, *, folder_id: str, **kwargs: Any) -> WebSearch:
        return cls(
            HttpxTransport(timeout=DEFAULT_TIMEOUT),
            StaticApiKeyProvider(api_key),
            folder_id=folder_id,
            **kwargs,
        )

    @classmethod
    def with_oauth_token(cls, oauth_token: str, *, folder_id: str, **kwargs: Any) -> WebSearch:
        transport = HttpxTransport(timeout=DEFAULT_TIMEOUT)
        return cls(
            transport,
            IamTokenProvider(transport, oauth_token=oauth_token),
            folder_id=folder_id,
            **kwargs,
        )

    async def search(self, query: str, *, limit: int) -> SearchResults:
        headers = await self._credentials.auth_header()
        body = {
            "query": {
                "searchType": "SEARCH_TYPE_RU",
                "queryText": query[:400],
                "familyMode": "FAMILY_MODE_STRICT",
            },
            # One document per site: a Тема researched from five pages of one domain is
            # one Источник, not five.
            "groupSpec": {
                "groupMode": "GROUP_MODE_DEEP",
                "groupsOnPage": str(limit),
                "docsInGroup": "1",
            },
            "maxPassages": "3",
            "l10n": "LOCALIZATION_RU",
            "folderId": self._folder_id,
            "responseFormat": "FORMAT_XML",
        }

        async def call() -> HttpResponse:
            return await self._transport.post(self._url, headers=headers, json=body)

        started = self._clock()
        response = await with_connection_retry(
            call, max_retries=self._max_retries, sleep=self._sleep
        )
        latency_ms = max(0, round((self._clock() - started) * 1000))
        if response.status != 200:
            raise map_error(response)
        try:
            raw = base64.b64decode(response.body["rawData"])
        except (KeyError, TypeError, binascii.Error) as exc:
            raise YandexError(f"Malformed web search response: {response.body!r:.300}") from exc
        return SearchResults(
            hits=parse_search_xml(raw)[:limit],
            latency_ms=latency_ms,
            cost=self._cost_per_request,
        )


def parse_search_xml(raw: bytes) -> list[SearchHit]:
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise YandexError("Malformed web search XML") from exc
    error = root.find("./response/error")
    if error is not None:
        if error.get("code") == _NOTHING_FOUND_CODE:
            return []
        raise YandexError(f"Web search error {error.get('code')}: {_text(error)}")
    hits: list[SearchHit] = []
    for doc in root.iterfind(".//results/grouping/group/doc"):
        url = _text(doc.find("url"))
        if not url:
            continue
        passages = [_text(p) for p in doc.iterfind("passages/passage")]
        snippet = " ".join(p for p in passages if p) or _text(doc.find("headline"))
        hits.append(SearchHit(url=url, title=_text(doc.find("title")), snippet=snippet))
    return hits


def _text(element: ET.Element | None) -> str:
    """Titles/passages wrap matched words in `<hlword>` - `itertext` flattens them back."""
    if element is None:
        return ""
    return " ".join("".join(element.itertext()).split())
