"""Persona type, Preset catalog, stored-value parsing, and default - the
Персона настройка (#50; moved here from `personas.py`, which now keeps only
Площадка profiles, per ADR-0009 - the author of a Статья is a Настройка, not
a Площадка concern).

The stored key stays `"voice"` (ADR-0009): it is an internal string of this
module, invisible to the Owner and to code outside this module's boundary.

`resolve_persona` reads one stored string into either a known Preset or a
free-text custom Персона - a legacy value that is neither a Preset marker
nor the pre-rename default title is trusted as the Owner's own description
(ADR-0010 will structure it; it stays a free string for this ticket).
"""

from __future__ import annotations

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


def resolve_persona(value: str | None) -> tuple[Persona | None, str | None]:
    if not value or value == DEFAULT_PERSONA_TITLE:
        return PERSONAS[DEFAULT_PERSONA_KEY], None
    if value.startswith(PERSONA_VALUE_PREFIX):
        persona = PERSONAS.get(value.removeprefix(PERSONA_VALUE_PREFIX))
        if persona is not None:
            return persona, None
    return None, value
