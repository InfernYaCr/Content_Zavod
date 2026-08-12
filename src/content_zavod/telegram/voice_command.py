"""handle_voice_command / handle_set_voice_command: Owner-only Voice view and edit.

`/set_voice <текст>` normalizes (strips surrounding whitespace) and validates
before touching the store - empty/whitespace-only input gets a plain error
reply and no side effects. Multi-line text is accepted as-is. `article_pipeline`
reads the new value straight from `OwnerSettingsStore` on its next call, so no
live process state needs updating here.

`/voice` also offers a hardcoded list of ready-made persona templates as inline
buttons (#39). Picking one calls `handle_set_voice_command` with the template's
full text - the same save path as typing `/set_voice <текст>` by hand - so there
is no separate dialog/FSM state to keep in sync, matching the bot's existing
stateless-button pattern (Plan pagination, export format, confirm/skip).
"""

from __future__ import annotations

from typing import Protocol

from ..personas import PERSONAS, persona_setting_value, resolve_persona
from ..pipelines.article_pipeline import DEFAULT_VOICE, VOICE_KEY
from .gateway import TelegramGateway, build_voice_keyboard

# (title, text) pairs shown as buttons under /voice. Lives in the command layer,
# not article_pipeline, since these are Telegram UI shortcuts for `/set_voice`,
# not part of the Voice domain itself.
VOICE_TEMPLATES: list[tuple[str, str]] = [
    (persona.title, persona_setting_value(persona.key)) for persona in PERSONAS.values()
]


class OwnerSettingsOperations(Protocol):
    async def get(self, key: str) -> str | None: ...

    async def set(self, key: str, value: str) -> None: ...


def _voice_title(value: str | None) -> str:
    if not value:
        return DEFAULT_VOICE
    persona, custom_voice = resolve_persona(value)
    return persona.title if persona is not None else (custom_voice or DEFAULT_VOICE)


async def handle_voice_command(
    settings_store: OwnerSettingsOperations, gateway: TelegramGateway, chat_id: int
) -> None:
    value = await settings_store.get(VOICE_KEY)
    await gateway.send_notice(
        chat_id,
        f"Текущий Голос: {_voice_title(value)}",
        reply_markup=build_voice_keyboard(VOICE_TEMPLATES),
    )


async def handle_voice_template_callback(
    settings_store: OwnerSettingsOperations,
    gateway: TelegramGateway,
    chat_id: int,
    template_index: int,
) -> None:
    _title, text = VOICE_TEMPLATES[template_index]
    await handle_set_voice_command(settings_store, gateway, chat_id, text)


async def handle_set_voice_command(
    settings_store: OwnerSettingsOperations, gateway: TelegramGateway, chat_id: int, args: str
) -> None:
    normalized = args.strip()
    if not normalized:
        await gateway.send_error(chat_id, "Использование: /set_voice <текст>")
        return

    await settings_store.set(VOICE_KEY, normalized)
    await gateway.send_notice(chat_id, f"Голос изменён: {_voice_title(normalized)}")
