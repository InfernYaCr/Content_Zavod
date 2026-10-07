import pytest
from aiogram.types import BotCommand, BotCommandScopeChat

from content_zavod.access import MemberView
from content_zavod.telegram.commands import (
    MENU_COMMANDS,
    commands_for_role,
    resync_member_commands,
    sync_commands,
)
from content_zavod.telegram.main_menu import render_help_text


class FakeBot:
    def __init__(self, failing: frozenset[int] = frozenset()) -> None:
        self.calls: list[tuple[list[BotCommand], BotCommandScopeChat]] = []
        self.failing = failing

    async def set_my_commands(self, commands, *, scope) -> None:
        if scope.chat_id in self.failing:
            raise RuntimeError("Bad Request: chat not found")
        self.calls.append((commands, scope))


@pytest.mark.parametrize("role", ["owner", "content_manager"])
def test_command_menu_is_just_menu_and_help_for_every_role(role) -> None:
    """#95: everything else is a button in the Главное меню; old commands are hidden aliases."""
    assert [c.command for c in commands_for_role(role)] == ["menu", "help"]


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["owner", "content_manager"])
async def test_sync_commands_overwrites_the_users_own_scope(role) -> None:
    bot = FakeBot()

    await sync_commands(bot, telegram_id=7, role=role)

    ((commands, scope),) = bot.calls
    assert commands == MENU_COMMANDS
    assert scope.chat_id == 7


@pytest.mark.asyncio
async def test_startup_resync_rewrites_every_members_scope_and_skips_failures() -> None:
    """#95: a member who never sends /start again must not keep the pre-#95 16-command list;
    one member the bot can't reach doesn't stop the others (or the bot's startup)."""
    bot = FakeBot(failing=frozenset({8}))
    members = [MemberView(7, "owner"), MemberView(8, "content_manager"), MemberView(9, "owner")]

    await resync_member_commands(bot, members, pause=0)

    assert [scope.chat_id for _commands, scope in bot.calls] == [7, 9]
    assert all(commands == MENU_COMMANDS for commands, _scope in bot.calls)


def test_help_for_content_manager_describes_the_shared_menu_only() -> None:
    text = render_help_text("content_manager")

    assert "/menu" in text
    assert "✍️ Предложить Тему" in text
    assert "⚙️ Настройки" not in text


def test_help_for_owner_also_describes_owner_buttons() -> None:
    text = render_help_text("owner")

    assert "⚙️ Настройки" in text
    assert "👥 Участники" in text
    assert "🕘 Расписание" in text
