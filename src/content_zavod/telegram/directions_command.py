"""handle_directions_command / handle_set_directions_command: Owner-only
Направления (Wordstat seed keyword list) view and edit.

`/set_directions <список через запятую>` splits on commas, strips each item,
and drops empties (from double/leading/trailing commas) before touching the
store - an empty list after normalization gets a plain error reply and no
side effects. Duplicates are kept as-is. `plan_pipeline` reads the new list
straight from `OwnerSettingsStore` on its next `generate_plan` call, so no
live process state needs updating here.
"""

from __future__ import annotations

from typing import Protocol

from ..pipelines.plan_pipeline import DEFAULT_DIRECTIONS, DIRECTIONS_KEY, parse_directions
from .gateway import TelegramGateway


class OwnerSettingsOperations(Protocol):
    async def get(self, key: str) -> str | None: ...

    async def set(self, key: str, value: str) -> None: ...


async def handle_directions_command(settings_store: OwnerSettingsOperations, gateway: TelegramGateway, chat_id: int) -> None:
    value = await settings_store.get(DIRECTIONS_KEY)
    directions = parse_directions(value) if value else list(DEFAULT_DIRECTIONS)
    await gateway.send_notice(chat_id, f"Текущие Направления: {', '.join(directions)}")


async def handle_set_directions_command(
    settings_store: OwnerSettingsOperations, gateway: TelegramGateway, chat_id: int, args: str
) -> None:
    normalized = parse_directions(args)
    if not normalized:
        await gateway.send_error(chat_id, "Использование: /set_directions <список через запятую>")
        return

    joined = ", ".join(normalized)
    await settings_store.set(DIRECTIONS_KEY, joined)
    await gateway.send_notice(chat_id, f"Направления изменены: {joined}")
