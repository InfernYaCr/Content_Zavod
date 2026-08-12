"""handle_persona_command / handle_set_persona_command: Owner-only Persona view and edit.

Renamed from voice_command.py (#50, per ADR-0009) - the stored key stays
`"voice"` inside `SettingsService`, invisible here. `/set_persona <текст>`
delegates normalization and validation to `SettingsService.set_persona` -
this command layer only turns its `InvalidSettingValue` into the Owner-facing
reply text, matching `niche_command`/`directions_command`. Multi-line text is
accepted as-is. `article_pipeline` reads the new value straight from
Настройки on its next call, so no live process state needs updating here.

`/persona` also offers the Preset catalog as inline buttons (#39, moved into
`settings.PERSONAS` by #50) - picking one calls `handle_set_persona_command`
with the Preset's stored value, the same save path as typing `/set_persona
<текст>` by hand, so there is no separate dialog/FSM state to keep in sync.
"""

from __future__ import annotations

from typing import Protocol

from ..domain.errors import InvalidSettingValue
from ..settings import PERSONAS, OwnerSettings, persona_setting_value, resolve_persona
from .gateway import TelegramGateway, build_persona_keyboard

# (title, stored value) pairs shown as buttons under /persona. Lives in the command
# layer, not settings.persona, since these are Telegram UI shortcuts for `/set_persona`,
# not part of the Persona catalog itself.
PERSONA_TEMPLATES: list[tuple[str, str]] = [
    (persona.title, persona_setting_value(persona.key)) for persona in PERSONAS.values()
]


class PersonaSettings(Protocol):
    async def read(self) -> OwnerSettings: ...

    async def set_persona(self, value: str) -> str: ...


def _persona_title(current: OwnerSettings) -> str:
    if current.persona is not None:
        return current.persona.title
    return current.custom_persona or ""


async def handle_persona_command(
    settings: PersonaSettings, gateway: TelegramGateway, chat_id: int
) -> None:
    current = await settings.read()
    await gateway.send_notice(
        chat_id,
        f"Текущая Персона: {_persona_title(current)}",
        reply_markup=build_persona_keyboard(PERSONA_TEMPLATES),
    )


async def handle_persona_template_callback(
    settings: PersonaSettings,
    gateway: TelegramGateway,
    chat_id: int,
    template_index: int,
) -> None:
    _title, value = PERSONA_TEMPLATES[template_index]
    await handle_set_persona_command(settings, gateway, chat_id, value)


async def handle_set_persona_command(
    settings: PersonaSettings, gateway: TelegramGateway, chat_id: int, args: str
) -> None:
    try:
        normalized = await settings.set_persona(args)
    except InvalidSettingValue:
        await gateway.send_error(chat_id, "Использование: /set_persona <текст>")
        return

    persona, custom_persona = resolve_persona(normalized)
    title = persona.title if persona is not None else (custom_persona or normalized)
    await gateway.send_notice(chat_id, f"Персона изменена: {title}")
