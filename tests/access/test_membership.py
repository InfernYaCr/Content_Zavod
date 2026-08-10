from __future__ import annotations

import pytest

from content_zavod.access import Membership, MemberNotFound


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

    await membership.remove_member(1)

    assert await membership.role_for(1) is None


async def test_remove_member_unknown_telegram_id_raises(membership: Membership) -> None:
    with pytest.raises(MemberNotFound):
        await membership.remove_member(999)
