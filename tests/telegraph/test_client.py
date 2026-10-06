from __future__ import annotations

import json
from urllib.parse import parse_qs

import httpx
import pytest

from content_zavod.telegraph import HttpxTelegraphClient, TelegraphError


def _client(handler) -> HttpxTelegraphClient:
    return HttpxTelegraphClient(transport=httpx.MockTransport(handler))


async def test_create_page_posts_form_with_json_content() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["form"] = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        return httpx.Response(
            200,
            json={"ok": True, "result": {"path": "T-10-07", "url": "https://telegra.ph/T-10-07"}},
        )

    client = _client(handler)
    page = await client.create_page(
        "tok", title="Заголовок", content=[{"tag": "p", "children": ["Привет"]}], author_name="A"
    )
    await client.aclose()

    assert page.path == "T-10-07"
    assert page.url == "https://telegra.ph/T-10-07"
    assert seen["url"] == "https://api.telegra.ph/createPage"
    form = seen["form"]
    assert form["access_token"] == "tok"
    assert form["title"] == "Заголовок"
    assert json.loads(form["content"]) == [{"tag": "p", "children": ["Привет"]}]


async def test_edit_page_targets_the_path() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, json={"ok": True, "result": {"path": "p", "url": "u"}})

    client = _client(handler)
    await client.edit_page("tok", "p", title="T", content=[], author_name="A")
    await client.aclose()

    assert seen == ["https://api.telegra.ph/editPage/p"]


async def test_create_account_returns_the_access_token() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True, "result": {"access_token": "new-tok"}})

    client = _client(handler)
    assert await client.create_account(short_name="s", author_name="A") == "new-tok"
    await client.aclose()


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, json={"ok": False, "error": "CONTENT_TOO_BIG"}),
        httpx.Response(502, text="bad gateway"),
    ],
)
async def test_api_errors_raise_telegraph_error(response: httpx.Response) -> None:
    client = _client(lambda request: response)

    with pytest.raises(TelegraphError):
        await client.create_page("tok", title="T", content=[], author_name="A")
    await client.aclose()


async def test_network_errors_raise_telegraph_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    client = _client(handler)

    with pytest.raises(TelegraphError):
        await client.create_account(short_name="s", author_name="A")
    await client.aclose()
