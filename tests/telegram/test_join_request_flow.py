from __future__ import annotations

import pytest

from content_zavod.access import JoinRequestBroadcast, JoinRequestView
from content_zavod.telegram.join_request_flow import JoinRequestFlow


class FakeRequests:
    def __init__(self) -> None:
        self._next_id = 1
        self._requests: dict[int, JoinRequestView] = {}
        self._broadcasts: dict[int, list[JoinRequestBroadcast]] = {}

    async def create(self, telegram_id: int, username: str | None) -> int:
        request_id = self._next_id
        self._next_id += 1
        self._requests[request_id] = JoinRequestView(
            id=request_id, telegram_id=telegram_id, username=username, status="pending", resolved_by=None
        )
        self._broadcasts[request_id] = []
        return request_id

    async def get(self, join_request_id: int) -> JoinRequestView:
        return self._requests[join_request_id]

    async def record_broadcast(
        self, join_request_id: int, owner_telegram_id: int, chat_id: int, message_id: int
    ) -> None:
        self._broadcasts[join_request_id].append(
            JoinRequestBroadcast(owner_telegram_id=owner_telegram_id, chat_id=chat_id, message_id=message_id)
        )

    async def broadcasts_for(self, join_request_id: int) -> list[JoinRequestBroadcast]:
        return self._broadcasts[join_request_id]

    async def resolve(self, join_request_id: int, *, approved: bool, resolved_by: int) -> JoinRequestView:
        current = self._requests[join_request_id]
        if current.status != "pending":
            return JoinRequestView(
                id=current.id,
                telegram_id=current.telegram_id,
                username=current.username,
                status=current.status,
                resolved_by=current.resolved_by,
                resolved_now=False,
            )
        updated = JoinRequestView(
            id=current.id,
            telegram_id=current.telegram_id,
            username=current.username,
            status="approved" if approved else "declined",
            resolved_by=resolved_by,
            resolved_now=True,
        )
        self._requests[join_request_id] = updated
        return updated


class FakeMembership:
    def __init__(self, owners: list[int]) -> None:
        self._owners = owners
        self.added: list[tuple[int, str]] = []

    async def list_by_role(self, role: str) -> list[int]:
        return self._owners if role == "owner" else []

    async def add_member(self, telegram_id: int, role: str) -> None:
        self.added.append((telegram_id, role))


class FakeGateway:
    def __init__(self) -> None:
        self._next_message_id = 1
        self.sent_messages: list[tuple[int, str]] = []
        self.edited: list[tuple[int, int, str]] = []

    async def send_message(self, chat_id: int, text: str, reply_markup=None) -> int:
        self.sent_messages.append((chat_id, text))
        message_id = self._next_message_id
        self._next_message_id += 1
        return message_id

    async def edit_notice(self, chat_id: int, message_id: int, text: str) -> None:
        self.edited.append((chat_id, message_id, text))


@pytest.mark.asyncio
async def test_request_access_broadcasts_to_every_owner() -> None:
    requests, membership, gateway = FakeRequests(), FakeMembership([10, 20]), FakeGateway()
    flow = JoinRequestFlow(requests, membership, gateway)

    await flow.request_access(telegram_id=100, username="alice")

    assert {chat_id for chat_id, _ in gateway.sent_messages} == {10, 20}
    view = await requests.get(1)
    broadcasts = await requests.broadcasts_for(view.id)
    assert {b.owner_telegram_id for b in broadcasts} == {10, 20}


@pytest.mark.asyncio
async def test_approve_grants_content_manager_and_notifies_requester() -> None:
    requests, membership, gateway = FakeRequests(), FakeMembership([10]), FakeGateway()
    flow = JoinRequestFlow(requests, membership, gateway)
    await flow.request_access(telegram_id=100, username="alice")

    await flow.handle_approve(resolver_id=10, resolver_name="Owner10", join_request_id=1)

    assert membership.added == [(100, "content_manager")]
    assert (100, "Доступ выдан. Нажмите /start ещё раз, чтобы увидеть доступные команды.") in gateway.sent_messages


@pytest.mark.asyncio
async def test_approve_edits_every_owners_broadcast_copy() -> None:
    requests, membership, gateway = FakeRequests(), FakeMembership([10, 20]), FakeGateway()
    flow = JoinRequestFlow(requests, membership, gateway)
    await flow.request_access(telegram_id=100, username="alice")

    await flow.handle_approve(resolver_id=10, resolver_name="Owner10", join_request_id=1)

    assert len(gateway.edited) == 2
    assert all("одобрена" in text for _, _, text in gateway.edited)


@pytest.mark.asyncio
async def test_decline_does_not_grant_membership() -> None:
    requests, membership, gateway = FakeRequests(), FakeMembership([10]), FakeGateway()
    flow = JoinRequestFlow(requests, membership, gateway)
    await flow.request_access(telegram_id=100, username="alice")

    await flow.handle_decline(resolver_id=10, resolver_name="Owner10", join_request_id=1)

    assert membership.added == []
    assert any("отклонена" in text for _, _, text in gateway.edited)


@pytest.mark.asyncio
async def test_second_owners_tap_is_a_safe_no_op() -> None:
    requests, membership, gateway = FakeRequests(), FakeMembership([10, 20]), FakeGateway()
    flow = JoinRequestFlow(requests, membership, gateway)
    await flow.request_access(telegram_id=100, username="alice")

    await flow.handle_approve(resolver_id=10, resolver_name="Owner10", join_request_id=1)
    edits_after_first = len(gateway.edited)
    grants_after_first = len(membership.added)

    await flow.handle_decline(resolver_id=20, resolver_name="Owner20", join_request_id=1)

    assert len(membership.added) == grants_after_first  # not granted twice, and not revoked
    # the second tap is a no-op: no further edits/grants happen
    assert len(gateway.edited) == edits_after_first
