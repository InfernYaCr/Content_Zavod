import pytest
from aiogram.types import BotCommand, BotCommandScopeChat

from content_zavod.telegram.commands import ADMIN_COMMANDS, MENU_COMMANDS, sync_admin_commands


class FakeBot:
    def __init__(self, failing: frozenset[int] = frozenset()) -> None:
        self.calls: list[tuple[list[BotCommand], BotCommandScopeChat]] = []
        self.failing = failing

    async def set_my_commands(self, commands, *, scope) -> None:
        if scope.chat_id in self.failing:
            raise RuntimeError("Bad Request: chat not found")
        self.calls.append((commands, scope))


def test_command_menu_is_start_menu_and_help() -> None:
    """#95: everything else is a button in the Главное меню; old commands are hidden aliases.
    /start stays listed for a chat whose history (and «Начать» button) was cleared."""
    assert [c.command for c in MENU_COMMANDS] == ["start", "menu", "help"]
    assert [c.command for c in ADMIN_COMMANDS] == ["start", "menu", "help", "admin"]


@pytest.mark.asyncio
async def test_admin_commands_go_to_each_admins_own_scope_and_skip_failures() -> None:
    """ADR-0015: /admin is listed only for a Глобальный администратор; one the bot can't reach
    (never opened the private chat) doesn't stop the others or the bot's startup."""
    bot = FakeBot(failing=frozenset({8}))

    await sync_admin_commands(bot, [7, 8, 9])

    assert [scope.chat_id for _commands, scope in bot.calls] == [7, 9]
    assert all(commands == ADMIN_COMMANDS for commands, _scope in bot.calls)
