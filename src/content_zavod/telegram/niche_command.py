"""handle_niche_command / handle_set_niche_command: Owner-only Niche view and edit.

Validation and normalization (strip surrounding whitespace, reject
empty/whitespace-only input) live in `SettingsService.set_niche` - this
command layer only turns its `InvalidSettingValue` into the Owner-facing
reply text. Multi-line text is accepted as-is. Unlike `/set_schedule`, there
is nothing else to reschedule: `plan_pipeline` reads the new value straight
from Настройки on its next Job run, so no live process state needs updating
here.
"""

from __future__ import annotations

from typing import Protocol

from ..domain.errors import InvalidSettingValue
from ..settings import OwnerSettings
from .gateway import TelegramGateway


class NicheSettings(Protocol):
    async def read(self) -> OwnerSettings: ...

    async def set_niche(self, value: str) -> str: ...


async def handle_niche_command(
    settings: NicheSettings, gateway: TelegramGateway, chat_id: int
) -> None:
    current = await settings.read()
    await gateway.send_notice(chat_id, f"Текущая Ниша: {current.niche}")


async def handle_set_niche_command(
    settings: NicheSettings, gateway: TelegramGateway, chat_id: int, args: str
) -> None:
    try:
        normalized = await settings.set_niche(args)
    except InvalidSettingValue:
        await gateway.send_error(chat_id, "Использование: /set_niche <текст>")
        return

    await gateway.send_notice(chat_id, f"Ниша изменена: {normalized}")
