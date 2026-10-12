"""OwnerSettings: frozen snapshot returned by `SettingsService.read()`."""

from __future__ import annotations

from dataclasses import dataclass

from .persona import CustomPersona, Persona
from .project import Project
from .style_profile import StyleProfile


@dataclass(frozen=True, slots=True)
class OwnerSettings:
    niche: str
    directions: tuple[str, ...]
    persona: Persona | None
    custom_persona: CustomPersona | None
    # Optional (#98): `None` means Статьи carry no project CTA, exactly as before.
    project: Project | None = None
    # Optional (#100): the reader portrait; `None` means Темы and Статьи are prompted as before.
    audience: str | None = None
    # Whether the Владелец has told their Ниша: `niche` falls back to `DEFAULT_NICHE` either way,
    # but a Plan is only drafted once it was told (the Расписание tick checks the same).
    niche_saved: bool = False
    # Optional (#134): drawn from the Владелец's own texts; when set, it is the Стиль автора the
    # prompts use in place of `persona`/`custom_persona`.
    style_profile: StyleProfile | None = None
