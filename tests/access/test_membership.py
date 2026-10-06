from __future__ import annotations

import pytest

from content_zavod.access import (
    CannotRemoveSelf,
    JoinRequests,
    LastOwnerRemoval,
    MemberNotFound,
    Membership,
    MemberView,
)


async def test_role_for_unknown_telegram_id_is_none(membership: Membership) -> None:
    assert await membership.role_for(1) is None


async def test_add_member_makes_role_for_return_the_role(membership: Membership) -> None:
    await membership.add_member(1, "owner")

    assert await membership.role_for(1) == "owner"


async def test_add_member_twice_updates_the_role(membership: Membership) -> None:
    await membership.add_member(1, "content_manager")
    await membership.add_member(1, "owner")

    assert await membership.role_for(1) == "owner"


async def test_remove_member_makes_role_for_return_none_again(membership: Membership) -> None:
    await membership.add_member(1, "owner")
    await membership.add_member(2, "content_manager")

    await membership.remove_member(2, removed_by=1)

    assert await membership.role_for(2) is None


async def test_remove_member_unknown_telegram_id_raises(membership: Membership) -> None:
    await membership.add_member(1, "owner")

    with pytest.raises(MemberNotFound):
        await membership.remove_member(999, removed_by=1)


async def test_remove_member_refuses_to_remove_yourself(membership: Membership) -> None:
    """#90: an Owner can't remove themselves, even with another Owner around."""
    await membership.add_member(1, "owner")
    await membership.add_member(2, "owner")

    with pytest.raises(CannotRemoveSelf):
        await membership.remove_member(1, removed_by=1)

    assert await membership.role_for(1) == "owner"


async def test_remove_member_refuses_to_remove_the_last_owner(membership: Membership) -> None:
    """#90: the bot is never left without a Владелец - e.g. two Owners removing each other at
    once: the second removal finds only its own target left."""
    await membership.add_member(1, "owner")

    with pytest.raises(LastOwnerRemoval):
        await membership.remove_member(1, removed_by=2)

    assert await membership.role_for(1) == "owner"


async def test_remove_member_removes_an_owner_while_another_remains(
    membership: Membership,
) -> None:
    await membership.add_member(1, "owner")
    await membership.add_member(2, "owner")

    await membership.remove_member(2, removed_by=1)

    assert await membership.list_by_role("owner") == [1]


async def test_list_by_role_returns_only_matching_members(membership: Membership) -> None:
    await membership.add_member(1, "owner")
    await membership.add_member(2, "content_manager")
    await membership.add_member(3, "owner")

    assert await membership.list_by_role("owner") == [1, 3]
    assert await membership.list_by_role("content_manager") == [2]


async def test_list_all_returns_every_member(membership: Membership) -> None:
    await membership.add_member(1, "owner")
    await membership.add_member(2, "content_manager")

    assert await membership.list_all() == [
        MemberView(telegram_id=1, role="owner"),
        MemberView(telegram_id=2, role="content_manager"),
    ]


async def test_list_all_takes_username_from_the_latest_approved_join_request(
    membership: Membership, join_requests: JoinRequests
) -> None:
    """#90: /members shows @username - from the latest approved заявка, ignoring declined
    ones; a hand-added Owner with no заявка has none."""
    older = await join_requests.create(2, "old_name")
    await join_requests.resolve(older, approved=True, resolved_by=1)
    newer = await join_requests.create(2, "new_name")
    await join_requests.resolve(newer, approved=True, resolved_by=1)
    declined = await join_requests.create(2, "declined_name")
    await join_requests.resolve(declined, approved=False, resolved_by=1)
    await membership.add_member(1, "owner")
    await membership.add_member(2, "content_manager")

    assert await membership.list_all() == [
        MemberView(telegram_id=1, role="owner", username=None),
        MemberView(telegram_id=2, role="content_manager", username="new_name"),
    ]
