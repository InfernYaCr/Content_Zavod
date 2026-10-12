"""Профиль стиля (#134): how the Владелец writes, described by the model from Образцы of their
own texts - the third way to set the Стиль автора, next to a Пресет and «✏️ Свой стиль».

Only the description is kept, never the texts it was drawn from: the traits (обращение, тон,
длина предложений, …), a `summary` addressed to the Владелец («Вы пишете …»), up to
`MAX_VOICE_EXAMPLES` short verbatim snippets of their voice, and their own typed
`corrections` («✏️ Поправить»). Stored as JSON under its own `workspace_settings` key; a
Профиль стиля present takes the Стиль автора's place in the prompts (`article_prompts`), and
picking a Пресет or «Свой стиль» clears it (`SettingsService.set_persona`).

Everything in a Профиль стиля came through the model from texts the bot fetched or was sent,
so it is untrusted text: the prompts get it only as INPUT_DATA, and every field is capped here -
the rendered profile at `STYLE_PROFILE_MAX_LENGTH`, like a typed Персона.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

STYLE_PROFILE_KEY = "style_profile"

STYLE_PROFILE_MAX_LENGTH = 1500
"""The rendered traits (`style_profile_text`), the same cap as a typed Персона."""
MAX_VOICE_EXAMPLES = 3
VOICE_EXAMPLE_MAX_LENGTH = 400
SUMMARY_MAX_LENGTH = 400
CORRECTIONS_MAX_LENGTH = 500
_TRAIT_MAX_LENGTH = 200

CONFIDENCE_LEVELS = ("low", "medium", "high")
SOURCES = ("articles", "posts")

# (key, label) in the order they are asked for, shown and put into prompts.
STYLE_TRAITS: Mapping[str, str] = {
    "address": "Обращение к читателю",
    "person": "От какого лица",
    "tone": "Тон",
    "formality": "Формальность",
    "humor": "Юмор",
    "sentences": "Предложения и абзацы",
    "devices": "Приёмы",
    "lexicon": "Характерные слова",
    "avoids": "Чего избегает",
    "emoji": "Эмодзи",
    "openings": "Как начинает",
    "endings": "Как заканчивает",
    "not_like": "Так не пишет",
}


@dataclass(frozen=True)
class StyleProfile:
    traits: tuple[tuple[str, str], ...]
    """(key of `STYLE_TRAITS`, value), only the ones that were described."""
    summary: str
    voice_examples: tuple[str, ...] = ()
    confidence: str = "medium"
    source: str = "articles"
    """`posts` - drawn from a channel's posts: only tone and lexicon carry over to a Статья."""
    corrections: str = ""


def _clean(value: object, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.split())[:limit].strip()


def style_profile_text(profile: StyleProfile) -> str:
    """The traits as «Label: value» lines, within `STYLE_PROFILE_MAX_LENGTH` - a trait that
    doesn't fit any more is left out whole."""
    lines: list[str] = []
    size = 0
    for key, value in profile.traits:
        line = f"{STYLE_TRAITS[key]}: {value}"
        if size + len(line) + 1 > STYLE_PROFILE_MAX_LENGTH:
            break
        lines.append(line)
        size += len(line) + 1
    return "\n".join(lines)


def style_profile_from_dict(data: object) -> StyleProfile | None:
    """A profile from its JSON form (stored, or a Job's output); `None` when it describes
    nothing. Every field is re-cleaned and capped, whoever wrote it."""
    if not isinstance(data, dict):
        return None
    raw_traits = data.get("traits")
    traits_data: dict[str, Any] = raw_traits if isinstance(raw_traits, dict) else {}
    traits = tuple(
        (key, value)
        for key in STYLE_TRAITS
        if (value := _clean(traits_data.get(key), _TRAIT_MAX_LENGTH))
    )
    summary = _clean(data.get("summary"), SUMMARY_MAX_LENGTH)
    if not traits and not summary:
        return None
    examples = data.get("voice_examples")
    voice_examples = tuple(
        cleaned
        for item in (examples if isinstance(examples, list) else [])
        if (cleaned := _clean(item, VOICE_EXAMPLE_MAX_LENGTH))
    )[:MAX_VOICE_EXAMPLES]
    confidence = data.get("confidence")
    source = data.get("source")
    return StyleProfile(
        traits=traits,
        summary=summary,
        voice_examples=voice_examples,
        confidence=confidence if confidence in CONFIDENCE_LEVELS else "medium",
        source=source if source in SOURCES else "articles",
        corrections=_clean(data.get("corrections"), CORRECTIONS_MAX_LENGTH),
    )


def style_profile_to_dict(profile: StyleProfile) -> dict[str, Any]:
    return {
        "traits": dict(profile.traits),
        "summary": profile.summary,
        "voice_examples": list(profile.voice_examples),
        "confidence": profile.confidence,
        "source": profile.source,
        "corrections": profile.corrections,
    }


def serialize_style_profile(profile: StyleProfile) -> str:
    return json.dumps(style_profile_to_dict(profile), ensure_ascii=False)


def parse_stored_style_profile(value: str | None) -> StyleProfile | None:
    """`None` for nothing stored, a cleared one (`""`) or anything unreadable."""
    if not value:
        return None
    try:
        return style_profile_from_dict(json.loads(value))
    except ValueError:
        return None
