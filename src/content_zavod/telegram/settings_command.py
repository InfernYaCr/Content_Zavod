"""handle_settings_command: Owner-only overview of Niche, Персона, and
Направления in one message.

Each value falls back to its pipeline default when unset, matching
`handle_niche_command`/`handle_persona_command`/`handle_directions_command`.
A short note next to each value states what it drives, so the owner doesn't
need to remember three separate view commands to get the full picture.
"""

from __future__ import annotations

from typing import Protocol

from ..pipelines.plan_pipeline import (
    DEFAULT_DIRECTIONS,
    DEFAULT_NICHE,
    DIRECTIONS_KEY,
    NICHE_KEY,
    parse_directions,
)
from ..settings import PERSONA_KEY, persona_display_title, resolve_persona
from .gateway import TelegramGateway


class OwnerSettingsOperations(Protocol):
    async def get(self, key: str) -> str | None: ...


async def handle_settings_command(
    settings_store: OwnerSettingsOperations, gateway: TelegramGateway, chat_id: int
) -> None:
    niche = await settings_store.get(NICHE_KEY)
    persona_raw = await settings_store.get(PERSONA_KEY)
    directions_raw = await settings_store.get(DIRECTIONS_KEY)
    directions = parse_directions(directions_raw) if directions_raw else list(DEFAULT_DIRECTIONS)

    persona, custom_persona = resolve_persona(persona_raw)
    persona_title = persona_display_title(persona, custom_persona)

    lines = [
        f"Ниша: {niche or DEFAULT_NICHE} (подбор/регенерация Темы)",
        f"Персона: {persona_title} (аутлайн/черновик Статьи)",
        f"Направления: {', '.join(directions)} (Wordstat-подбор растущих запросов)",
    ]
    await gateway.send_notice(chat_id, "\n".join(lines))
