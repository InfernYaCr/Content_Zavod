"""Telegraph API port (https://telegra.ph/api): the external dependency behind the
Страница для чтения. Production talks to api.telegra.ph via `HttpxTelegraphClient`; tests
inject a fake implementing `TelegraphClient`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from .nodes import Node

API_BASE_URL = "https://api.telegra.ph"
PAGE_BASE_URL = "https://telegra.ph"


class TelegraphError(Exception):
    """Telegraph answered `ok: false`, a non-2xx status, or something unparseable."""


@dataclass(frozen=True)
class TelegraphPage:
    path: str
    url: str


class TelegraphClient(Protocol):
    async def create_account(self, *, short_name: str, author_name: str) -> str:
        """Returns the new account's access token."""
        ...

    async def create_page(
        self, access_token: str, *, title: str, content: list[Node], author_name: str
    ) -> TelegraphPage: ...

    async def edit_page(
        self,
        access_token: str,
        path: str,
        *,
        title: str,
        content: list[Node],
        author_name: str,
    ) -> TelegraphPage: ...


def page_url(path: str) -> str:
    """A page's public URL from its stored path - what `createPage` returns as `url`."""
    return f"{PAGE_BASE_URL}/{path}"


class HttpxTelegraphClient:
    """Production adapter. Sends form-encoded requests with `content` as a JSON string, the
    shape the Telegraph API documents for both GET and POST. The short default timeout is
    deliberate: publishing runs inline with Article delivery, so a hung telegra.ph may delay
    a card by seconds, never stall it."""

    def __init__(
        self,
        *,
        timeout: float = 5.0,
        base_url: str = API_BASE_URL,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._client = httpx.AsyncClient(timeout=timeout, transport=transport)
        self._base_url = base_url

    async def create_account(self, *, short_name: str, author_name: str) -> str:
        result = await self._call(
            "createAccount", {"short_name": short_name, "author_name": author_name}
        )
        return str(result["access_token"])

    async def create_page(
        self, access_token: str, *, title: str, content: list[Node], author_name: str
    ) -> TelegraphPage:
        result = await self._call(
            "createPage", _page_params(access_token, title, content, author_name)
        )
        return _to_page(result)

    async def edit_page(
        self,
        access_token: str,
        path: str,
        *,
        title: str,
        content: list[Node],
        author_name: str,
    ) -> TelegraphPage:
        result = await self._call(
            f"editPage/{path}", _page_params(access_token, title, content, author_name)
        )
        return _to_page(result)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _call(self, method: str, params: dict[str, str]) -> dict[str, Any]:
        try:
            response = await self._client.post(f"{self._base_url}/{method}", data=params)
            body = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise TelegraphError(f"{method}: {exc!r}") from exc
        if response.status_code >= 400 or not body.get("ok"):
            raise TelegraphError(f"{method}: {response.status_code} {body.get('error')!r}")
        return body["result"]


def _page_params(
    access_token: str, title: str, content: list[Node], author_name: str
) -> dict[str, str]:
    return {
        "access_token": access_token,
        "title": title,
        "author_name": author_name,
        "content": json.dumps(content, ensure_ascii=False),
    }


def _to_page(result: dict[str, Any]) -> TelegraphPage:
    try:
        return TelegraphPage(path=str(result["path"]), url=str(result["url"]))
    except KeyError as exc:
        raise TelegraphError(f"page result without {exc}") from exc
