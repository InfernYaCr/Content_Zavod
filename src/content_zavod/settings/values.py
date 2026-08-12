"""OwnerSettings: frozen snapshot returned by `SettingsService.read()`.

Persona/Voice is out of scope for this module (#49) - it stays behind
`article_pipeline.VOICE_KEY`, read directly from `OwnerSettingsStore`.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class OwnerSettings:
    niche: str
    directions: tuple[str, ...]
