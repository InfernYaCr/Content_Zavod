"""OwnerSettings: frozen snapshot returned by `SettingsService.read()`."""

from __future__ import annotations

from dataclasses import dataclass

from .persona import Persona


@dataclass(frozen=True, slots=True)
class OwnerSettings:
    niche: str
    directions: tuple[str, ...]
    persona: Persona | None
    custom_persona: str | None
