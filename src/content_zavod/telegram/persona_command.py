"""handle_persona_command / handle_set_persona_command: Owner-only Persona view and edit.

Renamed from voice_command.py (#50, per ADR-0009) - the stored key stays
`"voice"` inside `SettingsService`, invisible here. `/set_persona` accepts
`Роль: …`-marked lines (#51, ADR-0010); normalization, parsing, and Роль
validation are delegated to `SettingsService.set_persona` - this command
layer only turns its `InvalidSettingValue` into the Owner-facing reply text,
matching `niche_command`/`directions_command`. `article_pipeline` reads the
new value straight from Настройки on its next call, so no live process state
needs updating here.

`/persona` prints a chosen Preset by title, and a Custom Персона in the same
marked-line format `/set_persona` accepts (`format_custom_persona`) so the
Owner can copy the output, edit one line, and send it back (#51). It also
offers the Preset catalog as inline buttons (#39, moved into
`settings.PERSONAS` by #50) - picking one calls `handle_set_persona_command`
with the Preset's stored value, the same save path as typing `/set_persona`
by hand, so there is no separate dialog/FSM state to keep in sync.
"""

from __future__ import annotations

from typing import Protocol

from ..domain.errors import InvalidSettingValue
from ..settings import (
    PERSONAS,
    OwnerSettings,
    persona_detail_text,
    persona_display_title,
    persona_setting_value,
    resolve_persona,
)
from .gateway import TelegramGateway, build_persona_keyboard

# (title, stored value) pairs shown as buttons under /persona. Lives in the command
# layer, not settings.persona, since these are Telegram UI shortcuts for `/set_persona`,
# not part of the Persona catalog itself.
PERSONA_TEMPLATES: list[tuple[str, str]] = [
    (persona.title, persona_setting_value(persona.key)) for persona in PERSONAS.values()
]

SET_PERSONA_USAGE = (
    "Использование: /set_persona\nРоль: <кто пишет статьи> (обязательно)\n"
    "Название, Аудитория, Тон, Запрещено — по желанию, каждое поле с новой строки."
)


class PersonaSettings(Protocol):
    async def read(self) -> OwnerSettings: ...

    async def set_persona(self, value: str) -> str: ...


def _persona_reply_text(current: OwnerSettings) -> str:
    detail = persona_detail_text(current.persona, current.custom_persona)
    if current.persona is not None:
        return f"Текущая Персона: {detail}"
    return f"Текущая Персона:\n{detail}"


async def handle_persona_command(
    settings: PersonaSettings, gateway: TelegramGateway, chat_id: int
) -> None:
    current = await settings.read()
    await gateway.send_notice(
        chat_id,
        _persona_reply_text(current),
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
        await gateway.send_error(chat_id, SET_PERSONA_USAGE)
        return

    persona, custom_persona = resolve_persona(normalized)
    title = persona_display_title(persona, custom_persona) or normalized
    await gateway.send_notice(chat_id, f"Персона изменена: {title}")
