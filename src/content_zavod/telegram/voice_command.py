"""handle_voice_command / handle_set_voice_command: Owner-only Voice view and edit.

`/set_voice <текст>` normalizes (strips surrounding whitespace) and validates
before touching the store - empty/whitespace-only input gets a plain error
reply and no side effects. Multi-line text is accepted as-is. `article_pipeline`
reads the new value straight from `OwnerSettingsStore` on its next call, so no
live process state needs updating here.
"""

from __future__ import annotations

from typing import Protocol

from ..pipelines.article_pipeline import DEFAULT_VOICE, VOICE_KEY
from .gateway import TelegramGateway


class OwnerSettingsOperations(Protocol):
    async def get(self, key: str) -> str | None: ...

    async def set(self, key: str, value: str) -> None: ...


async def handle_voice_command(settings_store: OwnerSettingsOperations, gateway: TelegramGateway, chat_id: int) -> None:
    value = await settings_store.get(VOICE_KEY)
    await gateway.send_notice(chat_id, f"Текущий Голос: {value or DEFAULT_VOICE}")


async def handle_set_voice_command(
    settings_store: OwnerSettingsOperations, gateway: TelegramGateway, chat_id: int, args: str
) -> None:
    normalized = args.strip()
    if not normalized:
        await gateway.send_error(chat_id, "Использование: /set_voice <текст>")
        return

    await settings_store.set(VOICE_KEY, normalized)
    await gateway.send_notice(chat_id, f"Голос изменён: {normalized}")
