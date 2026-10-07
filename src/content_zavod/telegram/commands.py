"""commands: the Telegram command menu (setMyCommands) - just /menu and /help since #95.

Everything else is a button in the Главное меню (`main_menu.py`), so a Владелец and a
Контент-менеджер see the same two commands; what differs is the menu's buttons. The old
commands (`/topic`, `/history`, `/settings`, `/set_niche`, …) still work as hidden aliases
for whoever has them in muscle memory or in older bot messages - they're just no longer
listed. `sync_commands` still writes a per-user `BotCommandScopeChat`: that is where every
existing user's old 16-command list lives, and only the same scope overwrites it. It is
called when a role is first known (at /start), right after a join request is approved, and
for every Участник at bot startup (`resync_member_commands`) - so a member who never sends
/start again doesn't keep the stale pre-#95 list forever.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable

from aiogram.types import BotCommand, BotCommandScopeChat

from ..access import MemberView, Role
from .gateway import BotClient

logger = logging.getLogger(__name__)

# Between two setMyCommands calls at startup: well under Telegram's ~30 requests/s per bot.
_RESYNC_PAUSE_SECONDS = 0.05

MENU_COMMANDS: list[BotCommand] = [
    BotCommand(command="menu", description="Главное меню"),
    BotCommand(command="help", description="Как пользоваться ботом"),
]


def commands_for_role(role: Role) -> list[BotCommand]:
    """The same two commands for every Role - kept per Role so a future Role-only command
    has an obvious place to go."""
    return MENU_COMMANDS


async def sync_commands(bot: BotClient, telegram_id: int, role: Role) -> None:
    await bot.set_my_commands(
        commands_for_role(role), scope=BotCommandScopeChat(chat_id=telegram_id)
    )


async def resync_member_commands(
    bot: BotClient, members: Iterable[MemberView], *, pause: float = _RESYNC_PAUSE_SECONDS
) -> None:
    """Rewrite every Участник's own command list at startup. One member's failure (never
    opened a private chat with the bot, flood limit) is logged and skipped - it must not stop
    the bot from starting; their /start fixes it later."""
    for index, member in enumerate(members):
        if index:
            await asyncio.sleep(pause)
        try:
            await sync_commands(bot, member.telegram_id, member.role)
        except Exception:
            logger.warning("could not sync commands for %s", member.telegram_id, exc_info=True)
