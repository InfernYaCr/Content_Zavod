"""KeywordStats: a narrow client to Wordstat via Yandex Search API.

Invariant (per issue #3): never raises when Wordstat is unavailable. A
failure for one keyword just means that keyword is missing from the
returned dict, so a Plan can still be generated without its numbers.
Caches frequencies internally so repeated lookups don't re-hit the API.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Callable

from .credentials import CredentialProvider
from .errors import YandexError
from .http import HttpTransport

WORDSTAT_URL = "https://searchapi.api.cloud.yandex.net/v2/wordstat"

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class KeywordStat:
    keyword: str
    frequency: int


class KeywordStats:
    def __init__(
        self,
        transport: HttpTransport,
        credentials: CredentialProvider,
        *,
        folder_id: str,
        cache_ttl: float = 3600.0,
        clock: Callable[[], float] = time.monotonic,
        url: str = WORDSTAT_URL,
    ) -> None:
        self._transport = transport
        self._credentials = credentials
        self._folder_id = folder_id
        self._cache_ttl = cache_ttl
        self._clock = clock
        self._url = url
        self._cache: dict[str, tuple[float, KeywordStat]] = {}

    async def keyword_stats(self, keywords: list[str]) -> dict[str, KeywordStat]:
        result: dict[str, KeywordStat] = {}
        for keyword in keywords:
            cached = self._cached(keyword)
            if cached is not None:
                result[keyword] = cached
                continue
            try:
                stat = await self._fetch(keyword)
            except Exception:  # noqa: BLE001 - Wordstat unavailability must never propagate
                logger.warning("Wordstat lookup failed for keyword %r", keyword, exc_info=True)
                continue
            self._cache[keyword] = (self._clock(), stat)
            result[keyword] = stat
        return result

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
        response = await self._transport.get(
            self._url,
            headers=headers,
            params={"folderId": self._folder_id, "text": keyword},
        )
        if response.status != 200:
            raise YandexError(f"Wordstat request failed (status={response.status})")
        try:
            frequency = int(response.body["frequency"])
        except (KeyError, TypeError, ValueError) as exc:
            raise YandexError(f"Malformed Wordstat response: {response.body}") from exc
        return KeywordStat(keyword=keyword, frequency=frequency)
