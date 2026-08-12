from __future__ import annotations

import pytest

from content_zavod.access import MemberView
from content_zavod.telegram.members_command import handle_members_command


class FakeMembership:
    def __init__(self, members: list[MemberView]) -> None:
        self._members = members

    async def list_all(self) -> list[MemberView]:
        return self._members


class FakeGateway:
    def __init__(self) -> None:
        self.sent_messages: list[tuple[int, str, object]] = []
        self.sent_notices: list[tuple[int, str]] = []

    async def send_message(self, chat_id, text, reply_markup=None) -> int:
        self.sent_messages.append((chat_id, text, reply_markup))
        return 1

    async def send_notice(self, chat_id, text) -> None:
        self.sent_notices.append((chat_id, text))


@pytest.mark.asyncio
async def test_lists_every_member_with_a_remove_button() -> None:
    members = [
        MemberView(telegram_id=1, role="owner"),
        MemberView(telegram_id=2, role="content_manager"),
    ]
    membership, gateway = FakeMembership(members), FakeGateway()

    await handle_members_command(membership, gateway, chat_id=99)

    assert len(gateway.sent_messages) == 1
    chat_id, text, keyboard = gateway.sent_messages[0]
    assert chat_id == 99
    assert "1" in text and "2" in text
    assert len(keyboard.inline_keyboard) == 2


@pytest.mark.asyncio
async def test_empty_member_list_sends_a_notice_instead_of_an_empty_keyboard() -> None:
    membership, gateway = FakeMembership([]), FakeGateway()

    await handle_members_command(membership, gateway, chat_id=99)

    assert gateway.sent_messages == []
    assert gateway.sent_notices == [(99, "Участников пока нет.")]
