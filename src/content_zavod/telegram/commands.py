"""commands: the per-role Telegram command menu (setMyCommands), plus /help text.

Owner-only commands are excluded from a Content-manager's menu entirely
(a per-user `BotCommandScopeChat`, not just an access check on invocation) -
Telegram can't scope commands within a single group chat, so this targets
each user's own chat with the bot. `sync_commands` is called once when a
role is first known (at /start) and again right after a join request is
approved, rather than on every interaction.
"""

from __future__ import annotations

from aiogram.types import BotCommand, BotCommandScopeChat

from ..access import Role
from .gateway import BotClient

SHARED_COMMANDS: list[BotCommand] = [
    BotCommand(command="topic", description="Предложить Тему"),
    BotCommand(command="generate_plan", description="Сгенерировать План вручную"),
    BotCommand(command="history", description="История Планов по неделям"),
    BotCommand(command="help", description="Список команд"),
]

OWNER_COMMANDS: list[BotCommand] = SHARED_COMMANDS + [
    BotCommand(command="members", description="Участники и доступ"),
    BotCommand(command="schedule", description="Текущее расписание Плана"),
    BotCommand(command="set_schedule", description="Изменить расписание Плана"),
    BotCommand(command="niche", description="Текущая Ниша"),
    BotCommand(command="set_niche", description="Изменить Нишу"),
    BotCommand(command="directions", description="Текущие Направления"),
    BotCommand(command="set_directions", description="Изменить Направления"),
]


def commands_for_role(role: Role) -> list[BotCommand]:
    return OWNER_COMMANDS if role == "owner" else SHARED_COMMANDS


async def sync_commands(bot: BotClient, telegram_id: int, role: Role) -> None:
    await bot.set_my_commands(commands_for_role(role), scope=BotCommandScopeChat(chat_id=telegram_id))


def render_help_text(role: Role) -> str:
    lines = ["Доступные команды:"]
    for command in commands_for_role(role):
        lines.append(f"/{command.command} — {command.description}")
    return "\n".join(lines)
