"""commands: the Telegram command menu (setMyCommands) - just /menu and /help since #95.

Everything else is a button in the Главное меню (`main_menu.py`). The old commands (`/topic`,
`/history`, `/settings`, `/set_niche`, …) still work as hidden aliases for whoever has them in
muscle memory or in older bot messages - they're just no longer listed. A Глобальный
администратор (`GLOBAL_ADMIN_TELEGRAM_IDS`, ADR-0015) also sees /admin: set in their own chat
scope at startup, so nobody else's list shows it.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable

from aiogram.types import BotCommand, BotCommandScopeChat

from .gateway import BotClient

logger = logging.getLogger(__name__)

MENU_COMMANDS: list[BotCommand] = [
    BotCommand(command="menu", description="Главное меню"),
    BotCommand(command="help", description="Как пользоваться ботом"),
]
ADMIN_COMMANDS: list[BotCommand] = [
    *MENU_COMMANDS,
    BotCommand(command="admin", description="Пространства и Лимиты"),
]


async def sync_admin_commands(bot: BotClient, admin_ids: Iterable[int]) -> None:
    """List /admin for each Глобальный администратор. One failure (never opened a private chat
    with the bot) is logged and skipped - it must not stop the bot from starting."""
    for telegram_id in admin_ids:
        try:
            await bot.set_my_commands(
                ADMIN_COMMANDS, scope=BotCommandScopeChat(chat_id=telegram_id)
            )
        except Exception:
            logger.warning("could not set admin commands for %s", telegram_id, exc_info=True)
