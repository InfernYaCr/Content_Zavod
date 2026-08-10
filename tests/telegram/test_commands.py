import pytest
from aiogram.types import BotCommand, BotCommandScopeChat

from content_zavod.telegram.commands import (
    OWNER_COMMANDS,
    SHARED_COMMANDS,
    commands_for_role,
    render_help_text,
    sync_commands,
)


class FakeBot:
    def __init__(self) -> None:
        self.calls: list[tuple[list[BotCommand], BotCommandScopeChat]] = []

    async def set_my_commands(self, commands, *, scope) -> None:
        self.calls.append((commands, scope))


def test_commands_for_role_owner_includes_owner_only_commands() -> None:
    commands = commands_for_role("owner")

    names = {c.command for c in commands}
    assert {"members", "schedule", "set_schedule"} <= names


def test_commands_for_role_content_manager_excludes_owner_only_commands() -> None:
    commands = commands_for_role("content_manager")

    names = {c.command for c in commands}
    assert names == {c.command for c in SHARED_COMMANDS}
    assert "members" not in names
    assert "schedule" not in names
    assert "set_schedule" not in names


@pytest.mark.asyncio
async def test_sync_commands_sets_owner_scope_for_owner() -> None:
    bot = FakeBot()

    await sync_commands(bot, telegram_id=1, role="owner")

    assert len(bot.calls) == 1
    commands, scope = bot.calls[0]
    assert commands == OWNER_COMMANDS
    assert scope.chat_id == 1


@pytest.mark.asyncio
async def test_sync_commands_sets_shared_scope_for_content_manager() -> None:
    bot = FakeBot()

    await sync_commands(bot, telegram_id=2, role="content_manager")

    commands, scope = bot.calls[0]
    assert commands == SHARED_COMMANDS
    assert scope.chat_id == 2


def test_render_help_text_lists_every_command_for_the_role() -> None:
    text = render_help_text("content_manager")

    assert "/topic" in text
    assert "/members" not in text


def test_render_help_text_for_owner_includes_owner_commands() -> None:
    text = render_help_text("owner")

    assert "/members" in text
