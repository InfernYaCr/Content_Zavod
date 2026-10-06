"""commands: the Telegram command menu (setMyCommands) - just /menu and /help since #95.

Everything else is a button in the Главное меню (`main_menu.py`), so a Владелец and a
Контент-менеджер see the same two commands; what differs is the menu's buttons. The old
commands (`/topic`, `/history`, `/settings`, `/set_niche`, …) still work as hidden aliases
for whoever has them in muscle memory or in older bot messages - they're just no longer
listed. `sync_commands` still writes a per-user `BotCommandScopeChat`: that is where every
existing user's old 16-command list lives, and only the same scope overwrites it. It is
called when a role is first known (at /start) and right after a join request is approved.
"""

from __future__ import annotations

from aiogram.types import BotCommand, BotCommandScopeChat

from ..access import Role
from .gateway import BotClient

MENU_COMMANDS: list[BotCommand] = [
    BotCommand(command="menu", description="Главное меню"),
    BotCommand(command="help", description="Что умеет бот"),
]


def commands_for_role(role: Role) -> list[BotCommand]:
    """The same two commands for every Role - kept per Role so a future Role-only command
    has an obvious place to go."""
    return MENU_COMMANDS


async def sync_commands(bot: BotClient, telegram_id: int, role: Role) -> None:
    await bot.set_my_commands(
        commands_for_role(role), scope=BotCommandScopeChat(chat_id=telegram_id)
    )
