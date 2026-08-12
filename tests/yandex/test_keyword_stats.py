import pytest

from content_zavod.yandex.credentials import StaticApiKeyProvider
from content_zavod.yandex.errors import YandexError
from content_zavod.yandex.http import HttpResponse
from content_zavod.yandex.keyword_stats import (
    DYNAMICS_URL,
    TOP_REQUESTS_URL,
    KeywordDynamicsPoint,
    KeywordStat,
    KeywordStats,
)

from .fakes import FakeClock, FakeHttpTransport, RecordingSleep


def _make_client(transport: FakeHttpTransport, **kwargs: object) -> KeywordStats:
    return KeywordStats(transport, StaticApiKeyProvider("key"), folder_id="folder-1", **kwargs)


def _top_requests_response(count: int) -> HttpResponse:
    return HttpResponse(
        status=200, body={"results": [{"count": count}], "associations": [], "totalCount": count}
    )


@pytest.mark.asyncio
async def test_returns_stats_for_available_keywords() -> None:
    transport = FakeHttpTransport()
    transport.queue_post(TOP_REQUESTS_URL, _top_requests_response(1200))
    client = _make_client(transport)

    result = await client.keyword_stats(["crm для малого бизнеса"])

    assert result == {
        "crm для малого бизнеса": KeywordStat(keyword="crm для малого бизнеса", frequency=1200)
    }
    url, _headers, body = transport.post_calls[0]
    assert url == TOP_REQUESTS_URL
    assert body == {"phrase": "crm для малого бизнеса", "numPhrases": 20, "folderId": "folder-1"}


@pytest.mark.asyncio
async def test_omits_unavailable_keyword_instead_of_raising() -> None:
    transport = FakeHttpTransport()
    transport.queue_post(TOP_REQUESTS_URL, _top_requests_response(500))
    transport.queue_post(TOP_REQUESTS_URL, HttpResponse(status=503, body={}))
    client = _make_client(transport)

    result = await client.keyword_stats(["ok keyword", "broken keyword"])

    assert result == {"ok keyword": KeywordStat(keyword="ok keyword", frequency=500)}
    assert "broken keyword" not in result


@pytest.mark.asyncio
async def test_transport_exception_is_swallowed_after_retries_exhausted() -> None:
    class ExplodingTransport(FakeHttpTransport):
        async def post(self, url, *, headers, json):  # type: ignore[override]
            raise ConnectionError("network unreachable")

    sleep = RecordingSleep()
    client = _make_client(ExplodingTransport(), max_retries=2, sleep=sleep)

    result = await client.keyword_stats(["anything"])

    assert result == {}
    assert len(sleep.delays) == 2


@pytest.mark.asyncio
async def test_retries_on_connection_error_then_succeeds() -> None:
    class FlakyTransport(FakeHttpTransport):
        def __init__(self) -> None:
            super().__init__()
            self._attempts = 0

        async def post(self, url, *, headers, json):  # type: ignore[override]
            self._attempts += 1
            if self._attempts == 1:
                raise ConnectionError("SSL error")
            return await super().post(url, headers=headers, json=json)

    transport = FlakyTransport()
    transport.queue_post(TOP_REQUESTS_URL, _top_requests_response(700))
    sleep = RecordingSleep()
    client = _make_client(transport, max_retries=2, sleep=sleep)

    result = await client.keyword_stats(["kw"])

    assert result == {"kw": KeywordStat(keyword="kw", frequency=700)}
    assert len(sleep.delays) == 1


@pytest.mark.asyncio
async def test_caches_frequency_within_ttl() -> None:
    transport = FakeHttpTransport()
    clock = FakeClock()
    transport.queue_post(TOP_REQUESTS_URL, _top_requests_response(100))
    client = _make_client(transport, cache_ttl=60.0, clock=clock)

    first = await client.keyword_stats(["kw"])
    clock.advance(30)
    second = await client.keyword_stats(["kw"])

    assert first == second
    assert len(transport.post_calls) == 1


@pytest.mark.asyncio
async def test_refetches_after_cache_expires() -> None:
    transport = FakeHttpTransport()
    clock = FakeClock()
    transport.queue_post(TOP_REQUESTS_URL, _top_requests_response(100))
    transport.queue_post(TOP_REQUESTS_URL, _top_requests_response(150))
    client = _make_client(transport, cache_ttl=60.0, clock=clock)

    await client.keyword_stats(["kw"])
    clock.advance(61)
    second = await client.keyword_stats(["kw"])

    assert second["kw"].frequency == 150
    assert len(transport.post_calls) == 2


@pytest.mark.asyncio
async def test_keyword_dynamics_returns_points() -> None:
    transport = FakeHttpTransport()
    transport.queue_post(
        DYNAMICS_URL,
        HttpResponse(
            status=200,
            body={
                "results": [
                    {"date": "2026-02-01T00:00:00Z", "count": 1000, "share": 0.1},
                    {"date": "2026-03-01T00:00:00Z", "count": 1200, "share": 0.12},
                ]
            },
        ),
    )
    client = _make_client(transport)

    result = await client.keyword_dynamics(
        "кофемашина",
        period="PERIOD_MONTHLY",
        from_date="2026-02-01T00:00:00Z",
        to_date="2026-03-31T00:00:00Z",
    )

    assert result == [
        KeywordDynamicsPoint(date="2026-02-01T00:00:00Z", count=1000, share=0.1),
        KeywordDynamicsPoint(date="2026-03-01T00:00:00Z", count=1200, share=0.12),
    ]
    url, _headers, body = transport.post_calls[0]
    assert url == DYNAMICS_URL
    assert body == {
        "phrase": "кофемашина",
        "period": "PERIOD_MONTHLY",
        "fromDate": "2026-02-01T00:00:00Z",
        "toDate": "2026-03-31T00:00:00Z",
        "folderId": "folder-1",
    }


@pytest.mark.asyncio
async def test_keyword_dynamics_raises_on_error_status() -> None:
    transport = FakeHttpTransport()
    transport.queue_post(
        DYNAMICS_URL, HttpResponse(status=400, body={"message": "InvalidArgument"})
    )
    client = _make_client(transport)

    with pytest.raises(YandexError):
        await client.keyword_dynamics(
            "кофемашина",
            period="PERIOD_MONTHLY",
            from_date="2026-02-15T00:00:00Z",
            to_date="2026-03-31T00:00:00Z",
        )
