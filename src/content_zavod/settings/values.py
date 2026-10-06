"""OwnerSettings: frozen snapshot returned by `SettingsService.read()`."""

from __future__ import annotations

from dataclasses import dataclass

from .persona import CustomPersona, Persona
from .project import Project


@dataclass(frozen=True, slots=True)
class OwnerSettings:
    niche: str
    directions: tuple[str, ...]
    persona: Persona | None
    custom_persona: CustomPersona | None
    # Optional (#98): `None` means Статьи carry no project CTA, exactly as before.
    project: Project | None = None
