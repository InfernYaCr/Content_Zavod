import pytest

from content_zavod.yandex.credentials import StaticApiKeyProvider
from content_zavod.yandex.errors import RateLimited, YandexError
from content_zavod.yandex.http import HttpResponse
from content_zavod.yandex.keyword_stats import (
    DYNAMICS_URL,
    TOP_REQUESTS_URL,
    KeywordDynamicsPoint,
    KeywordStat,
    KeywordStats,
    TopRequests,
)

from .fakes import FakeHttpTransport, RecordingSleep


def _make_client(transport: FakeHttpTransport, **kwargs: object) -> KeywordStats:
    return KeywordStats(transport, StaticApiKeyProvider("key"), folder_id="folder-1", **kwargs)


# Counts arrive as strings (live check, #128).
_TOP_BODY = {
    "totalCount": "37305",
    "results": [
        {"phrase": "домашняя выпечка", "count": "37305"},
        {"phrase": "выпечка рецепты", "count": "900"},
    ],
    "associations": [{"phrase": "что испечь", "count": "91912"}],
}


@pytest.mark.asyncio
async def test_top_requests_reads_frequency_results_and_associations() -> None:
    transport = FakeHttpTransport()
    transport.queue_post(TOP_REQUESTS_URL, HttpResponse(status=200, body=_TOP_BODY))

    result = await _make_client(transport).top_requests("домашняя выпечка", num_phrases=50)

    assert result == TopRequests(
        frequency=37305,
        results=[KeywordStat("домашняя выпечка", 37305), KeywordStat("выпечка рецепты", 900)],
        associations=[KeywordStat("что испечь", 91912)],
    )
    url, headers, body = transport.post_calls[0]
    assert (url, headers) == (TOP_REQUESTS_URL, {"Authorization": "Api-Key key"})
    assert body == {"phrase": "домашняя выпечка", "numPhrases": 50, "folderId": "folder-1"}


@pytest.mark.asyncio
async def test_a_malformed_top_response_raises() -> None:
    transport = FakeHttpTransport()
    transport.queue_post(
        TOP_REQUESTS_URL, HttpResponse(status=200, body={"results": [{"count": "x"}]})
    )

    with pytest.raises(YandexError):
        await _make_client(transport).top_requests("kw")


@pytest.mark.asyncio
async def test_a_429_is_waited_out_with_its_own_delay_then_retried() -> None:
    transport = FakeHttpTransport()
    transport.queue_post(TOP_REQUESTS_URL, HttpResponse(status=429, body={"code": 8}))
    transport.queue_post(TOP_REQUESTS_URL, HttpResponse(status=200, body=_TOP_BODY))
    sleep = RecordingSleep()

    result = await _make_client(transport, sleep=sleep, rate_limit_delay=3.0).top_requests("kw")

    assert result.frequency == 37305
    assert sleep.delays == [3.0]


@pytest.mark.asyncio
async def test_a_429_outlasting_the_retries_raises_rate_limited() -> None:
    transport = FakeHttpTransport()
    for _ in range(3):
        transport.queue_post(DYNAMICS_URL, HttpResponse(status=429, body={}))
    sleep = RecordingSleep()
    client = _make_client(transport, sleep=sleep, rate_limit_retries=2, rate_limit_delay=2.0)

    with pytest.raises(RateLimited):
        await client.keyword_dynamics(
            "kw",
            period="PERIOD_WEEKLY",
            from_date="2025-08-18T00:00:00Z",
            to_date="2026-10-18T00:00:00Z",
        )

    assert sleep.delays == [2.0, 4.0]
    assert len(transport.post_calls) == 3


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
    transport.queue_post(TOP_REQUESTS_URL, HttpResponse(status=200, body=_TOP_BODY))
    sleep = RecordingSleep()

    result = await _make_client(transport, max_retries=2, sleep=sleep).top_requests("kw")

    assert result.frequency == 37305
    assert len(sleep.delays) == 1


@pytest.mark.asyncio
async def test_keyword_dynamics_returns_points() -> None:
    transport = FakeHttpTransport()
    transport.queue_post(
        DYNAMICS_URL,
        HttpResponse(
            status=200,
            body={
                "results": [
                    {"date": "2025-08-18T00:00:00Z", "count": "7300", "share": 0.1},
                    {"date": "2025-08-25T00:00:00Z", "count": "7500", "share": 0.12},
                ]
            },
        ),
    )

    result = await _make_client(transport).keyword_dynamics(
        "домашняя выпечка",
        period="PERIOD_WEEKLY",
        from_date="2025-08-18T00:00:00Z",
        to_date="2026-10-18T00:00:00Z",
    )

    assert result == [
        KeywordDynamicsPoint(date="2025-08-18T00:00:00Z", count=7300, share=0.1),
        KeywordDynamicsPoint(date="2025-08-25T00:00:00Z", count=7500, share=0.12),
    ]
    url, _headers, body = transport.post_calls[0]
    assert url == DYNAMICS_URL
    assert body == {
        "phrase": "домашняя выпечка",
        "period": "PERIOD_WEEKLY",
        "fromDate": "2025-08-18T00:00:00Z",
        "toDate": "2026-10-18T00:00:00Z",
        "folderId": "folder-1",
    }


@pytest.mark.asyncio
async def test_keyword_dynamics_raises_on_error_status() -> None:
    transport = FakeHttpTransport()
    transport.queue_post(
        DYNAMICS_URL,
        HttpResponse(
            status=400, body={"code": 3, "message": "The from field value should be Monday"}
        ),
    )

    with pytest.raises(YandexError, match="Monday"):
        await _make_client(transport).keyword_dynamics(
            "кофемашина",
            period="PERIOD_WEEKLY",
            from_date="2025-08-20T00:00:00Z",
            to_date="2026-10-18T00:00:00Z",
        )
