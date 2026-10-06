"""handle_project_command / handle_set_project_command: Owner-only Проект view and edit (#98).

`/set_project <ссылка> <описание>`: link normalization (`@name`/`t.me/name` ->
`https://t.me/name`, otherwise `https://` only) and validation live in
`SettingsService.set_project` - this command layer only turns its
`InvalidSettingValue` into the Owner-facing reply text, matching
`niche_command`; `/set_project -` removes the Проект. `article_pipeline`
reads the new value straight from Настройки on its next Job run, so no live process state needs updating here.
"""

from __future__ import annotations

from typing import Protocol

from ..domain.errors import InvalidSettingValue
from ..settings import OwnerSettings, Project, project_detail_text
from .gateway import TelegramGateway

_USAGE = "Использование: /set_project <ссылка> <описание>\nУбрать Проект: /set_project -"
_BAD_LINK = "Ссылка не распознана. Подойдёт @канал, t.me/канал или https://адрес-сайта.\n" + _USAGE


class ProjectSettings(Protocol):
    async def read(self) -> OwnerSettings: ...

    async def set_project(self, value: str) -> Project | None: ...


async def handle_project_command(
    settings: ProjectSettings, gateway: TelegramGateway, chat_id: int
) -> None:
    current = await settings.read()
    await gateway.send_notice(chat_id, f"Текущий Проект: {project_detail_text(current.project)}")


async def handle_set_project_command(
    settings: ProjectSettings, gateway: TelegramGateway, chat_id: int, args: str
) -> None:
    try:
        project = await settings.set_project(args)
    except InvalidSettingValue as exc:
        await gateway.send_error(chat_id, _BAD_LINK if exc.field == "project_url" else _USAGE)
        return

    if project is None:
        await gateway.send_notice(chat_id, "Проект убран: Статьи будут без CTA.")
        return
    await gateway.send_notice(chat_id, f"Проект изменён: {project_detail_text(project)}")
