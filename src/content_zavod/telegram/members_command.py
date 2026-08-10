"""handle_members_command: Owner-only /members listing with an inline "Удалить" per row."""

from __future__ import annotations

from typing import Protocol

from ..access import MemberView
from .gateway import TelegramGateway, build_members_keyboard


class MembersOperations(Protocol):
    async def list_all(self) -> list[MemberView]: ...


async def handle_members_command(membership: MembersOperations, gateway: TelegramGateway, chat_id: int) -> None:
    members = await membership.list_all()
    if not members:
        await gateway.send_notice(chat_id, "Участников пока нет.")
        return
    lines = ["👥 Участники:"] + [f"{m.telegram_id} — {m.role}" for m in members]
    keyboard = build_members_keyboard([(m.telegram_id, m.role) for m in members])
    await gateway.send_message(chat_id, "\n".join(lines), reply_markup=keyboard)
