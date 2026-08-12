"""Площадка profiles for article generation.

Персона (type, Preset catalog, stored-value parsing, default) lives in
`settings/persona.py` (#50) - it describes the Настройка, not a Площадка.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class PlatformProfile:
    key: str
    title: str
    audience: str
    opening: str
    structure: str
    evidence_policy: str
    terminology: str
    cta: str
    forbidden_patterns: tuple[str, ...]


PLATFORM_PROFILES: Mapping[str, PlatformProfile] = {
    "zen": PlatformProfile(
        "zen",
        "Дзен",
        "широкая аудитория с разным уровнем подготовки",
        "понятная жизненная ситуация, вопрос или наблюдение; ценность ясна сразу",
        "повествовательная логика, короткие абзацы, информативные подзаголовки и примеры",
        "цифры и проверяемые факты сопровождаются подтверждёнными источниками",
        "профессиональные термины объясняются при первом появлении",
        "мягкий и релевантный продолжению темы",
        ("обманный кликбейт", "искусственная сенсационность", "длинное вступление без пользы"),
    ),
    "vc": PlatformProfile(
        "vc",
        "VC.ru",
        "предприниматели, специалисты и руководители",
        "деловой тезис, результат или проблема с контекстом и без пустого разогрева",
        "контекст → решение/механика → evidence → ограничения и риски → практический вывод",
        "цифры, рынок и причинные выводы требуют evidence; факт отделяется от мнения",
        "профессиональный язык допустим, неизвестные сокращения расшифровываются",
        "предметный вопрос к опыту читателей или следующий практический шаг",
        ("рекламная статья без пользы", "успешный успех", "цифры без источника"),
    ),
}


def platform_profile(platform: str) -> PlatformProfile:
    try:
        return PLATFORM_PROFILES[platform]
    except KeyError as exc:
        raise ValueError(f"Unknown platform: {platform}") from exc
