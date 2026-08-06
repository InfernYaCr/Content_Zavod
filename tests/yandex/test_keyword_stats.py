import pytest

from content_zavod.yandex.credentials import StaticApiKeyProvider
from content_zavod.yandex.http import HttpResponse
from content_zavod.yandex.keyword_stats import WORDSTAT_URL, KeywordStat, KeywordStats

from .fakes import FakeClock, FakeHttpTransport


def _make_client(transport: FakeHttpTransport, **kwargs: object) -> KeywordStats:
    return KeywordStats(transport, StaticApiKeyProvider("key"), folder_id="folder-1", **kwargs)


@pytest.mark.asyncio
async def test_returns_stats_for_available_keywords() -> None:
    transport = FakeHttpTransport()
    transport.queue_get(WORDSTAT_URL, HttpResponse(status=200, body={"frequency": 1200}))
    client = _make_client(transport)

    result = await client.keyword_stats(["crm для малого бизнеса"])

    assert result == {"crm для малого бизнеса": KeywordStat(keyword="crm для малого бизнеса", frequency=1200)}


@pytest.mark.asyncio
async def test_omits_unavailable_keyword_instead_of_raising() -> None:
    transport = FakeHttpTransport()
    transport.queue_get(WORDSTAT_URL, HttpResponse(status=200, body={"frequency": 500}))
    transport.queue_get(WORDSTAT_URL, HttpResponse(status=503, body={}))
    client = _make_client(transport)

    result = await client.keyword_stats(["ok keyword", "broken keyword"])

    assert result == {"ok keyword": KeywordStat(keyword="ok keyword", frequency=500)}
    assert "broken keyword" not in result


@pytest.mark.asyncio
async def test_transport_exception_is_swallowed() -> None:
    class ExplodingTransport(FakeHttpTransport):
        async def get(self, url, *, headers, params=None):  # type: ignore[override]
            raise ConnectionError("network unreachable")

    client = _make_client(ExplodingTransport())

    result = await client.keyword_stats(["anything"])

    assert result == {}


@pytest.mark.asyncio
async def test_caches_frequency_within_ttl() -> None:
    transport = FakeHttpTransport()
    clock = FakeClock()
    transport.queue_get(WORDSTAT_URL, HttpResponse(status=200, body={"frequency": 100}))
    client = _make_client(transport, cache_ttl=60.0, clock=clock)

    first = await client.keyword_stats(["kw"])
    clock.advance(30)
    second = await client.keyword_stats(["kw"])

    assert first == second
    assert len(transport.get_calls) == 1


@pytest.mark.asyncio
async def test_refetches_after_cache_expires() -> None:
    transport = FakeHttpTransport()
    clock = FakeClock()
    transport.queue_get(WORDSTAT_URL, HttpResponse(status=200, body={"frequency": 100}))
    transport.queue_get(WORDSTAT_URL, HttpResponse(status=200, body={"frequency": 150}))
    client = _make_client(transport, cache_ttl=60.0, clock=clock)

    await client.keyword_stats(["kw"])
    clock.advance(61)
    second = await client.keyword_stats(["kw"])

    assert second["kw"].frequency == 150
    assert len(transport.get_calls) == 2
