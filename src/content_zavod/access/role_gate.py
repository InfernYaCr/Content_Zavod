"""role_gate: the single pass/fail check behind every gated command.

`require_role` is pure - no gateway/aiogram dependency - so it is table-tested
directly instead of through a fake Telegram transport. `COMMAND_ROLE` is the
one place that says which of the gated commands need "owner" and which
accept any registered Role (since #95 only /menu and /help are listed in
Telegram's command menu - the rest are hidden aliases of menu buttons, gated
all the same); `start` is deliberately absent - it treats a
missing Role as its own welcome branch, not a denial (see ADR-0012).
"""

from __future__ import annotations

from .membership import Role

COMMAND_ROLE: dict[str, Role | None] = {
    "help": None,
    "menu": None,
    "topic": None,
    "generate_plan": None,
    "history": None,
    "members": "owner",
    "schedule": "owner",
    "set_schedule": "owner",
    "niche": "owner",
    "set_niche": "owner",
    "directions": "owner",
    "set_directions": "owner",
    "persona": "owner",
    "set_persona": "owner",
    "project": "owner",
    "set_project": "owner",
    "audience": "owner",
    "set_audience": "owner",
    "settings": "owner",
}


def require_role(actual: Role | None, required: Role | None) -> bool:
    """True when `actual` may run a command gated behind `required`.

    `required=None` means any registered Role suffices; an unregistered
    caller (`actual=None`) is always refused, regardless of `required`.
    """
    if actual is None:
        return False
    return required is None or actual == required
