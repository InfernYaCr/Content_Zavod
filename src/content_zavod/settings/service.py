"""SettingsService: typed read/write facade over `WorkspaceSettingsStore` for Ниша, Персона
(«Стиль автора» on screen), Проект («Ваш канал или сайт» on screen) and Аудитория. Every
method takes the Пространство first (ADR-0015): the bot passes the Workspace of the update, the
worker that of the claimed Job - one Workspace's Настройки never reach another's generations.

`read()` returns one frozen `OwnerSettings` snapshot with defaults already
applied, so callers (Job Handlers, the Экран Настроек) never touch raw store keys or repeat
fallback/resolution logic themselves.
Typed setters normalize (strip) internally and raise
`InvalidSettingValue` on input that normalizes to nothing, instead of
writing anything - the command layer decides how to phrase the rejection,
this module only decides whether a write happens. `set_persona` writes a
Preset marker as-is (from a template button); any other input is parsed as
`Роль: …`-marked Custom Персона lines (#51, ADR-0010) and stored as JSON -
missing Роль raises `InvalidSettingValue` without writing anything. `set_project` (#98) takes
`<ссылка> [описание]` (the description is optional, #133), normalizes the link via
`normalize_project_url` and raises `InvalidSettingValue("project_url")` for a link it can't
accept, or `InvalidSettingValue("project")` for empty input; a lone
`CLEAR_PROJECT` (`-`) removes the Проект, so Статьи go back to having no CTA.
`set_audience` (#100) stores the reader portrait as stripped free text, raising
`InvalidSettingValue("audience")` for empty input and `"audience_too_long"` past
`AUDIENCE_MAX_LENGTH`; a lone `CLEAR_AUDIENCE` (`-`) removes it.

Направления (#128) have no screen any more: the bot is to find its own queries from the Ниша
(the Niche Map). Until that pipeline lands, a stored `directions` value is still read and
seeds the Wordstat path of `generate_plan`; with none, Темы are drafted from the Ниша alone.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from functools import partial
from typing import Protocol

from ..domain.errors import InvalidSettingValue
from ..workspaces import WorkspaceId
from .audience import AUDIENCE_KEY, AUDIENCE_MAX_LENGTH, CLEAR_AUDIENCE, parse_stored_audience
from .persona import (
    PERSONA_KEY,
    PERSONA_VALUE_PREFIX,
    parse_custom_persona,
    resolve_persona,
    serialize_custom_persona,
)
from .project import (
    CLEAR_PROJECT,
    PROJECT_KEY,
    Project,
    normalize_project_url,
    parse_stored_project,
    serialize_project,
)
from .values import OwnerSettings

NICHE_KEY = "niche"
DEFAULT_NICHE = "маркетинг"

DIRECTIONS_KEY = "directions"

# Every key a Настройка is stored under: any of them present means the Владелец has already
# configured something, so the first-run wizard isn't forced on them (see `onboarding.py`).
SETTING_STORE_KEYS: tuple[str, ...] = (
    NICHE_KEY,
    AUDIENCE_KEY,
    PERSONA_KEY,
    PROJECT_KEY,
)


def parse_directions(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


async def _read_settings(get: Callable[[str], Awaitable[str | None]]) -> OwnerSettings:
    niche_raw = await get(NICHE_KEY)
    directions_raw = await get(DIRECTIONS_KEY)
    persona_raw = await get(PERSONA_KEY)
    project_raw = await get(PROJECT_KEY)
    audience_raw = await get(AUDIENCE_KEY)
    niche = niche_raw if niche_raw else DEFAULT_NICHE
    directions = parse_directions(directions_raw) if directions_raw else None
    persona, custom_persona = resolve_persona(persona_raw)
    return OwnerSettings(
        niche=niche,
        directions=tuple(directions or ()),
        persona=persona,
        custom_persona=custom_persona,
        project=parse_stored_project(project_raw),
        audience=parse_stored_audience(audience_raw),
        niche_saved=bool(niche_raw),
    )


class _WorkspaceSettingsStoreOperations(Protocol):
    async def get(self, workspace_id: WorkspaceId, key: str) -> str | None: ...

    async def set(self, workspace_id: WorkspaceId, key: str, value: str) -> None: ...


class SettingsReader(Protocol):
    """What a Job Handler reads its Настройки through: always those of the Job's own
    Пространство, named by the worker, never by the payload (ADR-0015)."""

    async def read(self, workspace_id: WorkspaceId) -> OwnerSettings: ...


class SettingsService:
    """Satisfies `SettingsReader` too: the worker reads any Job's Пространство through it."""

    def __init__(self, store: _WorkspaceSettingsStoreOperations) -> None:
        self._store = store

    async def read(self, workspace_id: WorkspaceId) -> OwnerSettings:
        return await _read_settings(partial(self._store.get, workspace_id))

    async def set_niche(self, workspace_id: WorkspaceId, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise InvalidSettingValue("niche")
        await self._store.set(workspace_id, NICHE_KEY, normalized)
        return normalized

    async def set_persona(self, workspace_id: WorkspaceId, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise InvalidSettingValue("persona")
        if normalized.startswith(PERSONA_VALUE_PREFIX):
            await self._store.set(workspace_id, PERSONA_KEY, normalized)
            return normalized
        try:
            custom_persona = parse_custom_persona(normalized)
        except ValueError as exc:
            raise InvalidSettingValue("persona") from exc
        serialized = serialize_custom_persona(custom_persona)
        await self._store.set(workspace_id, PERSONA_KEY, serialized)
        return serialized

    async def set_project(self, workspace_id: WorkspaceId, value: str) -> Project | None:
        if value.strip() == CLEAR_PROJECT:
            await self._store.set(workspace_id, PROJECT_KEY, "")
            return None
        parts = value.split(maxsplit=1)
        if not parts:
            raise InvalidSettingValue("project")
        try:
            url = normalize_project_url(parts[0])
        except ValueError as exc:
            raise InvalidSettingValue("project_url") from exc
        project = Project(url=url, description=parts[1].strip() if len(parts) > 1 else "")
        await self._store.set(workspace_id, PROJECT_KEY, serialize_project(project))
        return project

    async def set_audience(self, workspace_id: WorkspaceId, value: str) -> str | None:
        normalized = value.strip()
        if normalized == CLEAR_AUDIENCE:
            await self._store.set(workspace_id, AUDIENCE_KEY, "")
            return None
        if not normalized:
            raise InvalidSettingValue("audience")
        if len(normalized) > AUDIENCE_MAX_LENGTH:
            raise InvalidSettingValue("audience_too_long")
        await self._store.set(workspace_id, AUDIENCE_KEY, normalized)
        return normalized
