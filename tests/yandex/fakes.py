"""Fakes for the HttpTransport port, shared across yandex client tests."""

from __future__ import annotations

from typing import Any

from content_zavod.yandex.http import HttpResponse


class FakeHttpTransport:
    def __init__(self) -> None:
        self._post_responses: dict[str, list[HttpResponse]] = {}
        self._get_responses: dict[str, list[HttpResponse]] = {}
        self.post_calls: list[tuple[str, dict[str, str], dict[str, Any]]] = []
        self.get_calls: list[tuple[str, dict[str, str], dict[str, Any] | None]] = []

    def queue_post(self, url: str, response: HttpResponse) -> None:
        self._post_responses.setdefault(url, []).append(response)

    def queue_get(self, url: str, response: HttpResponse) -> None:
        self._get_responses.setdefault(url, []).append(response)

    async def post(
        self, url: str, *, headers: dict[str, str], json: dict[str, Any]
    ) -> HttpResponse:
        self.post_calls.append((url, headers, json))
        queue = self._post_responses.get(url)
        if not queue:
            raise AssertionError(f"no queued POST response for {url}")
        return queue.pop(0)

    async def get(
        self, url: str, *, headers: dict[str, str], params: dict[str, Any] | None = None
    ) -> HttpResponse:
        self.get_calls.append((url, headers, params))
        queue = self._get_responses.get(url)
        if not queue:
            raise AssertionError(f"no queued GET response for {url}")
        return queue.pop(0)


class RecordingSleep:
    def __init__(self) -> None:
        self.delays: list[float] = []

    async def __call__(self, delay: float) -> None:
        self.delays.append(delay)


class FakeClock:
    def __init__(self, start: float = 0.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds
