from __future__ import annotations

import pytest

from content_zavod.access.role_gate import COMMAND_ROLE, require_role

OWNER_COMMANDS = {
    "members",
    "schedule",
    "set_schedule",
    "niche",
    "set_niche",
    "directions",
    "set_directions",
    "persona",
    "set_persona",
    "settings",
}
ANY_ROLE_COMMANDS = {"help", "topic", "generate_plan", "history"}


@pytest.mark.parametrize(
    ("actual", "required", "expected"),
    [
        (None, None, False),
        (None, "owner", False),
        (None, "content_manager", False),
        ("owner", None, True),
        ("owner", "owner", True),
        ("owner", "content_manager", False),
        ("content_manager", None, True),
        ("content_manager", "owner", False),
        ("content_manager", "content_manager", True),
    ],
)
def test_require_role(actual, required, expected) -> None:
    assert require_role(actual, required) is expected


def test_command_role_covers_exactly_the_fourteen_gated_commands() -> None:
    assert set(COMMAND_ROLE) == OWNER_COMMANDS | ANY_ROLE_COMMANDS


def test_command_role_marks_owner_commands_owner_only() -> None:
    assert {command for command, role in COMMAND_ROLE.items() if role == "owner"} == OWNER_COMMANDS


def test_command_role_marks_shared_commands_as_any_registered_role() -> None:
    assert {command for command, role in COMMAND_ROLE.items() if role is None} == ANY_ROLE_COMMANDS


def test_command_role_excludes_start() -> None:
    assert "start" not in COMMAND_ROLE
