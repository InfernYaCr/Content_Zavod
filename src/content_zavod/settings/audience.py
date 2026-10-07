"""Аудитория: the Owner's portrait of the reader (#100) - who reads the Статьи, their pains,
goals and level - as opposed to the Персона, who writes them.

Optional free text: with nothing stored, `OwnerSettings.audience` is `None` and Темы and
Статьи are generated exactly as before. It reaches the prompts only as INPUT_DATA, never as
instruction text. Capped in length because it rides along in every Тема and Статья prompt.
"""

from __future__ import annotations

AUDIENCE_KEY = "audience"
# `-` removes the Аудитория (like Проект's); stored as an empty value, which reads back as `None`.
CLEAR_AUDIENCE = "-"
AUDIENCE_MAX_LENGTH = 1000
_SCREEN_LENGTH = 100


def parse_stored_audience(value: str | None) -> str | None:
    return (value or "").strip() or None


def audience_detail_text(audience: str | None) -> str:
    """The full value, for the question that asks for a new one."""
    return audience or "не задана"


def audience_screen_text(audience: str | None) -> str:
    """One short line for the Экран Настроек: line breaks folded, long text cut with «…»."""
    if audience is None:
        return "не задана"
    line = " ".join(audience.split())
    if len(line) <= _SCREEN_LENGTH:
        return line
    return line[:_SCREEN_LENGTH].rstrip() + "…"
