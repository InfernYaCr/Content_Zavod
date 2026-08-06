"""Transport seam shared by every Yandex API client.

This is the category-4 "true external dependency" port (see DEEPENING.md):
production talks to Yandex over HTTP via `HttpxTransport`, tests inject a
fake implementing the same `HttpTransport` protocol.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

import httpx


@dataclass(frozen=True)
class HttpResponse:
    status: int
    body: dict[str, Any]


class HttpTransport(Protocol):
    async def post(
        self, url: str, *, headers: dict[str, str], json: dict[str, Any]
    ) -> HttpResponse: ...

    async def get(
        self,
        url: str,
        *,
        headers: dict[str, str],
        params: dict[str, Any] | None = None,
    ) -> HttpResponse: ...


class HttpxTransport:
    """Production adapter backed by `httpx.AsyncClient`."""

    def __init__(self, *, timeout: float = 30.0) -> None:
        self._client = httpx.AsyncClient(timeout=timeout)

    async def post(
        self, url: str, *, headers: dict[str, str], json: dict[str, Any]
    ) -> HttpResponse:
        response = await self._client.post(url, headers=headers, json=json)
        return HttpResponse(status=response.status_code, body=_safe_json(response))

    async def get(
        self,
        url: str,
        *,
        headers: dict[str, str],
        params: dict[str, Any] | None = None,
    ) -> HttpResponse:
        response = await self._client.get(url, headers=headers, params=params)
        return HttpResponse(status=response.status_code, body=_safe_json(response))

    async def aclose(self) -> None:
        await self._client.aclose()


def _safe_json(response: httpx.Response) -> dict[str, Any]:
    try:
        return response.json()
    except ValueError:
        return {}
