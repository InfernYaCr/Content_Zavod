"""handle_directions_command / handle_set_directions_command: Owner-only
Направления (Wordstat seed keyword list) view and edit.

Splitting on commas, stripping each item, and dropping empties (from
double/leading/trailing commas) live in `SettingsService.set_directions` -
this command layer only turns its `InvalidSettingValue` into the
Owner-facing reply text. Duplicates are kept as-is. `plan_pipeline` reads
the new list straight from Настройки on its next `generate_plan` Job run,
so no live process state needs updating here.
"""

from __future__ import annotations

from typing import Protocol

from ..domain.errors import InvalidSettingValue
from ..settings import OwnerSettings
from .gateway import TelegramGateway


class DirectionsSettings(Protocol):
    async def read(self) -> OwnerSettings: ...

    async def set_directions(self, value: str) -> list[str]: ...


async def handle_directions_command(
    settings: DirectionsSettings, gateway: TelegramGateway, chat_id: int
) -> None:
    current = await settings.read()
    await gateway.send_notice(chat_id, f"Текущие Направления: {', '.join(current.directions)}")


async def handle_set_directions_command(
    settings: DirectionsSettings, gateway: TelegramGateway, chat_id: int, args: str
) -> None:
    try:
        normalized = await settings.set_directions(args)
    except InvalidSettingValue:
        await gateway.send_error(chat_id, "Использование: /set_directions <список через запятую>")
        return

    await gateway.send_notice(chat_id, f"Направления изменены: {', '.join(normalized)}")
