"""handle_niche_command / handle_set_niche_command: Owner-only Niche view and edit.

`/set_niche <текст>` normalizes (strips surrounding whitespace) and validates
before touching the store - empty/whitespace-only input gets a plain error
reply and no side effects. Multi-line text is accepted as-is. Unlike
`/set_schedule`, there is nothing else to reschedule: `plan_pipeline` reads
the new value straight from `OwnerSettingsStore` on its next call, so no
live process state needs updating here.
"""

from __future__ import annotations

from typing import Protocol

from ..pipelines.plan_pipeline import DEFAULT_NICHE, NICHE_KEY
from .gateway import TelegramGateway


class OwnerSettingsOperations(Protocol):
    async def get(self, key: str) -> str | None: ...

    async def set(self, key: str, value: str) -> None: ...


async def handle_niche_command(settings_store: OwnerSettingsOperations, gateway: TelegramGateway, chat_id: int) -> None:
    value = await settings_store.get(NICHE_KEY)
    await gateway.send_notice(chat_id, f"Текущая Ниша: {value or DEFAULT_NICHE}")


async def handle_set_niche_command(
    settings_store: OwnerSettingsOperations, gateway: TelegramGateway, chat_id: int, args: str
) -> None:
    normalized = args.strip()
    if not normalized:
        await gateway.send_error(chat_id, "Использование: /set_niche <текст>")
        return

    await settings_store.set(NICHE_KEY, normalized)
    await gateway.send_notice(chat_id, f"Ниша изменена: {normalized}")
