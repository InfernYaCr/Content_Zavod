"""Persona type, Preset catalog, stored-value parsing, and default - the
Персона настройка (#50; moved here from `personas.py`, which now keeps only
Площадка profiles, per ADR-0009 - the author of a Статья is a Настройка, not
a Площадка concern).

The stored key stays `"voice"` (ADR-0009): it is an internal string of this
module, invisible to the Owner and to code outside this module's boundary.

`resolve_persona` reads one stored string into either a known Preset or a
custom `CustomPersona` (ADR-0010). Three stored forms are tolerated: a Preset
marker resolves to a catalog `Persona`; a JSON object resolves to a
structured `CustomPersona`; any other non-empty string is the pre-#51 legacy
value, read as the Role field of a `CustomPersona` with no other field set.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass

PERSONA_KEY = "voice"
DEFAULT_PERSONA_KEY = "practical_marketer"
DEFAULT_PERSONA_TITLE = "маркетолог-практик"
PERSONA_VALUE_PREFIX = "preset:"


@dataclass(frozen=True)
class Persona:
    key: str
    title: str
    role: str
    audience: str
    tone: str
    expertise: str
    style: str
    vocabulary: str
    cta_style: str
    forbidden_patterns: tuple[str, ...]


PERSONAS: Mapping[str, Persona] = {
    "practical_marketer": Persona(
        "practical_marketer",
        "Маркетолог-практик",
        "практикующий маркетолог, отвечающий за результат, а не за красивые термины",
        "владельцы малого бизнеса и руководители маркетинга без лишнего времени",
        "уверенный, спокойный, доброжелательный; без менторства и искусственного восторга",
        "объясняет решение через задачу, механику, ограничения и измеримый результат",
        "короткие абзацы, конкретные примеры; сложный термин сразу объясняется",
        "живой деловой русский; числа только при наличии evidence",
        "один реалистичный следующий шаг без давления",
        ("вода", "канцелярит", "гарантированный результат", "выдуманные кейсы"),
    ),
    "evidence_analyst": Persona(
        "evidence_analyst",
        "Доказательный аналитик",
        "аналитик-редактор, отделяющий наблюдение, факт, оценку и гипотезу",
        "руководители и специалисты, принимающие решения по данным",
        "точный, сдержанный и честный в отношении неопределённости",
        "показывает методику, допущения, альтернативные объяснения и границы применимости",
        "тезис → evidence → интерпретация → ограничение; таблицы только когда полезны",
        "точные термины; оценки и гипотезы явно помечаются",
        "проверить вывод на данных читателя или провести небольшой эксперимент",
        ("ложная точность", "подмена корреляции причинностью", "неподтверждённые цифры"),
    ),
    "founder_operator": Persona(
        "founder_operator",
        "Фаундер-оператор",
        "руководитель, лично строящий процессы и описывающий проверенные решения",
        "предприниматели, продуктовые команды и руководители функций",
        "прямой, энергичный, самокритичный; без культа успеха",
        "раскрывает контекст решения, компромиссы, ошибки, стоимость и эффект",
        "сильный тезис; конкретный ход работы; итог и урок; первое лицо только к месту",
        "простой деловой язык; детали вместо стартап-клише",
        "предметный вопрос или повторяемый шаг из кейса",
        ("успешный успех", "героизация переработок", "выдуманный личный опыт"),
    ),
    "clear_teacher": Persona(
        "clear_teacher",
        "Понятный наставник",
        "опытный специалист, помогающий читателю разобраться и сделать самостоятельно",
        "заинтересованные читатели без глубокой подготовки в теме",
        "уважительный, ясный и поддерживающий; без разговора свысока",
        "строит объяснение от знакомого к новому, приводит пример и предупреждает об ошибках",
        "одна мысль на абзац; пошаговая логика; определения рядом с термином",
        "понятный русский без необязательного жаргона и англицизмов",
        "безопасный первый шаг или короткий чек-лист самопроверки",
        ("сюсюканье", "очевидно", "просто сделайте", "перегрузка терминами"),
    ),
}


def persona_setting_value(key: str) -> str:
    if key not in PERSONAS:
        raise ValueError(f"Unknown persona: {key}")
    return f"{PERSONA_VALUE_PREFIX}{key}"


@dataclass(frozen=True)
class CustomPersona:
    """The Owner's own Персона (#51, ADR-0010): five fields of `Persona`,
    only Роль required. Absent fields are `None`, never backfilled from the
    default Preset - `/persona` and the prompt block must show exactly what
    the Owner set, nothing more."""

    title: str | None
    role: str
    audience: str | None
    tone: str | None
    forbidden: str | None


# (field, label) in the fixed order used both for parsing `/set_persona` input
# and for printing it back via `format_custom_persona` - the two must stay in
# lockstep so `/persona`'s output is valid `/set_persona` input.
_CUSTOM_PERSONA_FIELDS: tuple[tuple[str, str], ...] = (
    ("title", "Название"),
    ("role", "Роль"),
    ("audience", "Аудитория"),
    ("tone", "Тон"),
    ("forbidden", "Запрещено"),
)
_LABEL_TO_FIELD: Mapping[str, str] = {
    label.lower(): field for field, label in _CUSTOM_PERSONA_FIELDS
}


def parse_custom_persona(text: str) -> CustomPersona:
    """Parse `Роль: …`-style marked lines. Raises `ValueError` when no Роль
    line is present - the caller (`SettingsService.set_persona`) turns that
    into the domain `InvalidSettingValue` without writing anything."""

    fields: dict[str, str] = {}
    for line in text.splitlines():
        label, sep, value = line.partition(":")
        if not sep:
            continue
        field = _LABEL_TO_FIELD.get(label.strip().lower())
        value = value.strip()
        if field and value:
            fields[field] = value

    role = fields.get("role")
    if not role:
        raise ValueError("Персона: поле 'Роль' обязательно")
    return CustomPersona(
        title=fields.get("title"),
        role=role,
        audience=fields.get("audience"),
        tone=fields.get("tone"),
        forbidden=fields.get("forbidden"),
    )


def format_custom_persona(persona: CustomPersona) -> str:
    """Marked lines matching `parse_custom_persona`'s input format, with
    absent fields omitted - the round-trip `/persona` copy-paste-edit shape."""

    values = {
        "title": persona.title,
        "role": persona.role,
        "audience": persona.audience,
        "tone": persona.tone,
        "forbidden": persona.forbidden,
    }
    return "\n".join(
        f"{label}: {values[field]}" for field, label in _CUSTOM_PERSONA_FIELDS if values[field]
    )


def custom_persona_label(persona: CustomPersona) -> str:
    return persona.title or persona.role


def persona_display_title(persona: Persona | None, custom_persona: CustomPersona | None) -> str:
    """The one-line title callers show for whichever Персона is active - a
    Preset's `title`, or a Custom Персона's `custom_persona_label`. Shared by
    `/set_persona`'s confirmation and `/settings`'s summary line so the two
    don't each re-derive the same persona/custom_persona switch."""

    if persona is not None:
        return persona.title
    return custom_persona_label(custom_persona) if custom_persona is not None else ""


def persona_detail_text(persona: Persona | None, custom_persona: CustomPersona | None) -> str:
    """The full Персона value as text for whichever Персона is active - a
    Preset's `title`, or a Custom Персона's fields via `format_custom_persona`.
    Shared by `/persona` and `/settings` so a change to how a Custom Персона's
    body is rendered doesn't need editing both command modules in lockstep."""

    if persona is not None:
        return persona.title
    return format_custom_persona(custom_persona) if custom_persona is not None else ""


def serialize_custom_persona(persona: CustomPersona) -> str:
    payload = {
        field: value
        for field, value in (
            ("title", persona.title),
            ("role", persona.role),
            ("audience", persona.audience),
            ("tone", persona.tone),
            ("forbidden", persona.forbidden),
        )
        if value
    }
    return json.dumps(payload, ensure_ascii=False)


def _parse_stored_custom_persona(value: str) -> CustomPersona | None:
    try:
        data = json.loads(value)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    role = data.get("role")
    if not isinstance(role, str) or not role.strip():
        return None

    def _text(key: str) -> str | None:
        item = data.get(key)
        return item if isinstance(item, str) and item.strip() else None

    return CustomPersona(
        title=_text("title"),
        role=role,
        audience=_text("audience"),
        tone=_text("tone"),
        forbidden=_text("forbidden"),
    )


def resolve_persona(value: str | None) -> tuple[Persona | None, CustomPersona | None]:
    if not value or value == DEFAULT_PERSONA_TITLE:
        return PERSONAS[DEFAULT_PERSONA_KEY], None
    if value.startswith(PERSONA_VALUE_PREFIX):
        persona = PERSONAS.get(value.removeprefix(PERSONA_VALUE_PREFIX))
        if persona is not None:
            return persona, None
    custom = _parse_stored_custom_persona(value)
    if custom is not None:
        return None, custom
    return None, CustomPersona(title=None, role=value, audience=None, tone=None, forbidden=None)
