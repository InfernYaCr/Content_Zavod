"""handle_members_command: Owner-only /members listing with an inline "Удалить" per row.

Each row shows the member's @username (falling back to the telegram id) and Role in Russian
(#90). "Удалить" doesn't remove right away: `redraw_members` re-renders the same message with
that row asking «Да, удалить / Отмена», and again after either answer.
"""

from __future__ import annotations

from typing import Protocol

from aiogram.types import InlineKeyboardMarkup

from ..access import MemberView
from .gateway import TelegramGateway, build_members_keyboard

_ROLE_TITLES = {"owner": "Владелец", "content_manager": "Контент-менеджер"}


class MembersOperations(Protocol):
    async def list_all(self) -> list[MemberView]: ...


class MessageEditor(Protocol):
    async def edit_message_text(
        self,
        chat_id: int,
        message_id: int,
        text: str,
        reply_markup: InlineKeyboardMarkup | None = None,
    ) -> None: ...


def _member_label(member: MemberView) -> str:
    return f"@{member.username}" if member.username else str(member.telegram_id)


def _render_members(
    members: list[MemberView], *, confirm_id: int | None = None
) -> tuple[str, InlineKeyboardMarkup]:
    lines = ["👥 Участники:"] + [
        f"{_member_label(m)} — {_ROLE_TITLES.get(m.role, m.role)}" for m in members
    ]
    keyboard = build_members_keyboard(
        [(m.telegram_id, _member_label(m)) for m in members], confirm_id=confirm_id
    )
    return "\n".join(lines), keyboard


async def handle_members_command(
    membership: MembersOperations, gateway: TelegramGateway, chat_id: int
) -> None:
    members = await membership.list_all()
    if not members:
        await gateway.send_notice(chat_id, "Участников пока нет.")
        return
    text, keyboard = _render_members(members)
    await gateway.send_message(chat_id, text, reply_markup=keyboard)


async def redraw_members(
    membership: MembersOperations,
    editor: MessageEditor,
    chat_id: int,
    message_id: int,
    *,
    confirm_id: int | None = None,
) -> None:
    """Re-renders an already sent /members message in place from the current list (#90)."""
    text, keyboard = _render_members(await membership.list_all(), confirm_id=confirm_id)
    await editor.edit_message_text(chat_id, message_id, text, reply_markup=keyboard)
