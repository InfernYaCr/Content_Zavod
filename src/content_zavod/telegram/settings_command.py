"""handle_settings_command: Owner-only overview of Ниша, Персона, and
Направления in one message.

Reads all three Настройки with one `SettingsReader.read()` call - Настройки
owns the defaults and the stored-value parsing, so this command layer never
touches store key names or default values, matching
`handle_niche_command`/`handle_persona_command`/`handle_directions_command`.
The Персона line shows the same value `/persona` shows (a Preset's title, or
a Custom Персона's fields via `format_custom_persona`), so the summary can't
drift from what the dedicated command reports. A short note next to each
value states what it drives, so the Owner doesn't need to remember three
separate view commands to get the full picture.
"""

from __future__ import annotations

from ..settings import SettingsReader, persona_detail_text
from .gateway import TelegramGateway


async def handle_settings_command(
    settings: SettingsReader, gateway: TelegramGateway, chat_id: int
) -> None:
    current = await settings.read()

    lines = [
        f"Ниша: {current.niche} (подбор/регенерация Темы)",
        "Персона (аутлайн/черновик Статьи):",
        persona_detail_text(current.persona, current.custom_persona),
        f"Направления: {', '.join(current.directions)} (Wordstat-подбор растущих запросов)",
    ]
    await gateway.send_notice(chat_id, "\n".join(lines))
