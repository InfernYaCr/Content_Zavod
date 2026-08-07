"""UrlReachabilityChecker: technical (HTTP 200) reachability probe for the
Источник links an Article cites at generation time.

Not a Yandex API, so it doesn't live under `yandex/` and doesn't use the
JSON-only `HttpTransport` port built for those clients - source URLs are
arbitrary external sites returning arbitrary (non-JSON) bodies.
"""

from __future__ import annotations

from typing import Protocol

import httpx


class UrlReachabilityChecker(Protocol):
    async def is_reachable(self, url: str) -> bool: ...


class HttpxUrlReachabilityChecker:
    """Production adapter. Never raises: any failure just means "not reachable"."""

    def __init__(self, *, timeout: float = 10.0) -> None:
        self._client = httpx.AsyncClient(timeout=timeout, follow_redirects=True)

    async def is_reachable(self, url: str) -> bool:
        try:
            response = await self._client.head(url)
            if response.status_code == 405:
                response = await self._client.get(url)
        except httpx.HTTPError:
            return False
        return response.status_code == 200

    async def aclose(self) -> None:
        await self._client.aclose()
