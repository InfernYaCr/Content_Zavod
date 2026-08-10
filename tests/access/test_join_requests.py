from __future__ import annotations

import pytest

from content_zavod.access import JoinRequestNotFound, JoinRequests, JoinRequestView


async def test_create_then_get_round_trip(join_requests: JoinRequests) -> None:
    request_id = await join_requests.create(100, "alice")

    view = await join_requests.get(request_id)

    assert view == JoinRequestView(
        id=request_id, telegram_id=100, username="alice", status="pending", resolved_by=None
    )


async def test_get_raises_for_unknown_request(join_requests: JoinRequests) -> None:
    with pytest.raises(JoinRequestNotFound):
        await join_requests.get(999)


async def test_resolve_approves_and_records_who_resolved_it(join_requests: JoinRequests) -> None:
    request_id = await join_requests.create(100, "alice")

    view = await join_requests.resolve(request_id, approved=True, resolved_by=1)

    assert view.status == "approved"
    assert view.resolved_by == 1


async def test_resolve_declines(join_requests: JoinRequests) -> None:
    request_id = await join_requests.create(100, "alice")

    view = await join_requests.resolve(request_id, approved=False, resolved_by=1)

    assert view.status == "declined"


async def test_resolve_is_idempotent_a_second_owner_gets_the_first_resolution_back(
    join_requests: JoinRequests,
) -> None:
    request_id = await join_requests.create(100, "alice")

    first = await join_requests.resolve(request_id, approved=True, resolved_by=1)
    second = await join_requests.resolve(request_id, approved=False, resolved_by=2)

    assert second == first
    assert second.status == "approved"
    assert second.resolved_by == 1


async def test_resolve_raises_for_unknown_request(join_requests: JoinRequests) -> None:
    with pytest.raises(JoinRequestNotFound):
        await join_requests.resolve(999, approved=True, resolved_by=1)


async def test_broadcasts_for_returns_every_recorded_target(join_requests: JoinRequests) -> None:
    request_id = await join_requests.create(100, "alice")
    await join_requests.record_broadcast(request_id, owner_telegram_id=1, chat_id=1, message_id=10)
    await join_requests.record_broadcast(request_id, owner_telegram_id=2, chat_id=2, message_id=20)

    broadcasts = await join_requests.broadcasts_for(request_id)

    assert {(b.owner_telegram_id, b.chat_id, b.message_id) for b in broadcasts} == {
        (1, 1, 10),
        (2, 2, 20),
    }


async def test_broadcasts_for_returns_empty_list_when_none_recorded(
    join_requests: JoinRequests,
) -> None:
    request_id = await join_requests.create(100, "alice")

    assert await join_requests.broadcasts_for(request_id) == []
