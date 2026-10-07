import base64

import pytest

from content_zavod.yandex.credentials import StaticApiKeyProvider
from content_zavod.yandex.errors import AuthError, YandexError
from content_zavod.yandex.http import HttpResponse
from content_zavod.yandex.web_search import WEB_SEARCH_URL, SearchHit, WebSearch

from .fakes import FakeHttpTransport, RecordingSleep

_XML = """<?xml version="1.0" encoding="utf-8"?>
<yandexsearch version="1.0">
<response date="20261006T120000">
<found priority="all">2</found>
<results><grouping attr="d" mode="deep" groups-on-page="10" docs-in-group="1">
<group><doc id="1">
  <url>https://example.ru/crm-guide</url><domain>example.ru</domain>
  <title>Как выбрать <hlword>CRM</hlword> для бизнеса</title>
  <headline>Заголовок</headline>
  <passages><passage>Первый <hlword>фрагмент</hlword>.</passage><passage>Второй.</passage></passages>
</doc></group>
<group><doc id="2">
  <url>https://other.ru/a</url><domain>other.ru</domain>
  <title>Другая статья</title><headline>Только headline</headline>
</doc></group>
</grouping></results>
</response>
</yandexsearch>"""


def _ok(xml: str) -> HttpResponse:
    return HttpResponse(
        status=200, body={"rawData": base64.b64encode(xml.encode("utf-8")).decode("ascii")}
    )


def _client(transport: FakeHttpTransport, **kwargs: object) -> WebSearch:
    return WebSearch(transport, StaticApiKeyProvider("key"), folder_id="folder-1", **kwargs)


@pytest.mark.asyncio
async def test_search_decodes_xml_results_into_hits() -> None:
    transport = FakeHttpTransport()
    transport.queue_post(WEB_SEARCH_URL, _ok(_XML))

    results = await _client(transport, cost_per_request=0.5).search("выбор crm", limit=5)

    assert results.hits == [
        SearchHit(
            url="https://example.ru/crm-guide",
            title="Как выбрать CRM для бизнеса",
            snippet="Первый фрагмент. Второй.",
        ),
        SearchHit(url="https://other.ru/a", title="Другая статья", snippet="Только headline"),
    ]
    assert results.cost == 0.5
    url, headers, body = transport.post_calls[0]
    assert url == WEB_SEARCH_URL
    assert headers == {"Authorization": "Api-Key key"}
    assert body["query"]["queryText"] == "выбор crm"
    assert body["folderId"] == "folder-1"
    assert body["responseFormat"] == "FORMAT_XML"
    assert body["groupSpec"]["groupsOnPage"] == "5"
    assert body["region"] == "225"
    assert body["l10n"] == "LOCALIZATION_RU"


@pytest.mark.asyncio
async def test_search_cost_is_unknown_without_configured_pricing() -> None:
    transport = FakeHttpTransport()
    transport.queue_post(WEB_SEARCH_URL, _ok(_XML))

    results = await _client(transport).search("q", limit=1)

    assert results.cost is None
    assert len(results.hits) == 1


@pytest.mark.asyncio
async def test_nothing_found_is_an_empty_result_not_an_error() -> None:
    transport = FakeHttpTransport()
    transport.queue_post(
        WEB_SEARCH_URL,
        _ok(
            '<yandexsearch><response><error code="15">Искомая комбинация слов нигде не '
            "встречается</error></response></yandexsearch>"
        ),
    )

    results = await _client(transport).search("q", limit=5)

    assert results.hits == []


@pytest.mark.asyncio
async def test_other_xml_error_raises() -> None:
    transport = FakeHttpTransport()
    transport.queue_post(
        WEB_SEARCH_URL,
        _ok('<yandexsearch><response><error code="32">limit</error></response></yandexsearch>'),
    )

    with pytest.raises(YandexError, match="32"):
        await _client(transport).search("q", limit=5)


@pytest.mark.asyncio
async def test_rejected_credentials_raise_auth_error() -> None:
    transport = FakeHttpTransport()
    transport.queue_post(WEB_SEARCH_URL, HttpResponse(status=403, body={"message": "denied"}))

    with pytest.raises(AuthError):
        await _client(transport).search("q", limit=5)


@pytest.mark.asyncio
async def test_malformed_response_raises() -> None:
    transport = FakeHttpTransport()
    transport.queue_post(WEB_SEARCH_URL, HttpResponse(status=200, body={}))

    with pytest.raises(YandexError):
        await _client(transport).search("q", limit=5)


@pytest.mark.asyncio
async def test_connection_errors_are_retried() -> None:
    class FlakyTransport(FakeHttpTransport):
        def __init__(self) -> None:
            super().__init__()
            self.attempts = 0

        async def post(self, url, *, headers, json):  # type: ignore[override]
            self.attempts += 1
            if self.attempts == 1:
                raise ConnectionError("ssl")
            return await super().post(url, headers=headers, json=json)

    transport = FlakyTransport()
    transport.queue_post(WEB_SEARCH_URL, _ok(_XML))
    sleep = RecordingSleep()

    results = await _client(transport, sleep=sleep).search("q", limit=5)

    assert len(results.hits) == 2
    assert transport.attempts == 2
