"""SettingsService: typed read/write facade over `OwnerSettingsStore` for
Ниша and Направления - the two Настройки this module owns (#49; Персона is
untouched, it stays behind `article_pipeline.VOICE_KEY`).

`read()` returns one frozen `OwnerSettings` snapshot with defaults already
applied, so callers (Job Handlers, `/niche`, `/directions`) never touch raw
store keys or repeat fallback logic themselves. Typed setters normalize
(strip / split-on-comma) internally and raise `InvalidSettingValue` on input
that normalizes to nothing, instead of writing anything - the command layer
decides how to phrase the rejection, this module only decides whether a
write happens.

`plan_pipeline` re-exports the constants and `parse_directions` below as
aliases so existing importers (`/settings`, `/set_niche`, `/set_directions`)
keep working unmodified; this module is the single place that defines them.
"""

from __future__ import annotations

from typing import Protocol

from ..domain.errors import InvalidSettingValue
from .values import OwnerSettings

NICHE_KEY = "niche"
DEFAULT_NICHE = "маркетинг"

DIRECTIONS_KEY = "directions"
DEFAULT_DIRECTIONS: tuple[str, ...] = (
    "crm для малого бизнеса",
    "email маркетинг",
    "таргетированная реклама",
    "контент маркетинг",
    "seo продвижение сайта",
    "воронка продаж",
    "юнит экономика",
    "маркетинговая стратегия",
)


def parse_directions(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


class _OwnerSettingsStoreOperations(Protocol):
    async def get(self, key: str) -> str | None: ...

    async def set(self, key: str, value: str) -> None: ...


class SettingsReader(Protocol):
    async def read(self) -> OwnerSettings: ...


class SettingsService:
    def __init__(self, store: _OwnerSettingsStoreOperations) -> None:
        self._store = store

    async def read(self) -> OwnerSettings:
        niche_raw = await self._store.get(NICHE_KEY)
        directions_raw = await self._store.get(DIRECTIONS_KEY)
        niche = niche_raw if niche_raw else DEFAULT_NICHE
        directions = parse_directions(directions_raw) if directions_raw else None
        return OwnerSettings(niche=niche, directions=tuple(directions or DEFAULT_DIRECTIONS))

    async def set_niche(self, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise InvalidSettingValue("niche")
        await self._store.set(NICHE_KEY, normalized)
        return normalized

    async def set_directions(self, value: str) -> list[str]:
        normalized = parse_directions(value)
        if not normalized:
            raise InvalidSettingValue("directions")
        await self._store.set(DIRECTIONS_KEY, ", ".join(normalized))
        return normalized
