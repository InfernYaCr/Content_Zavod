import pytest

from content_zavod.settings import (
    DEFAULT_PERSONA_KEY,
    PERSONAS,
    CustomPersona,
    custom_persona_label,
    format_custom_persona,
    parse_custom_persona,
    persona_setting_value,
    resolve_persona,
    serialize_custom_persona,
)


def test_default_resolves_to_full_practical_marketer_persona() -> None:
    persona, custom_persona = resolve_persona(None)

    assert persona == PERSONAS[DEFAULT_PERSONA_KEY]
    assert persona.audience
    assert persona.forbidden_patterns
    assert custom_persona is None


def test_known_preset_roundtrips_through_setting_value() -> None:
    persona, custom_persona = resolve_persona(persona_setting_value("evidence_analyst"))

    assert persona == PERSONAS["evidence_analyst"]
    assert custom_persona is None


def test_legacy_voice_remains_supported_as_custom_data_read_as_role() -> None:
    persona, custom_persona = resolve_persona("технооптимист-фаундер")

    assert persona is None
    assert custom_persona == CustomPersona(
        title=None, role="технооптимист-фаундер", audience=None, tone=None, forbidden=None
    )


def test_parse_custom_persona_reads_only_role_when_that_is_all_that_is_given() -> None:
    custom = parse_custom_persona("Роль: технооптимист-фаундер")

    assert custom == CustomPersona(
        title=None, role="технооптимист-фаундер", audience=None, tone=None, forbidden=None
    )


def test_parse_custom_persona_reads_all_five_marked_fields() -> None:
    text = (
        "Название: Технооптимист\n"
        "Роль: фаундер, объясняющий технологические решения просто\n"
        "Аудитория: технические руководители\n"
        "Тон: энергичный и прямой\n"
        "Запрещено: хайп, буллшит-бинго"
    )

    custom = parse_custom_persona(text)

    assert custom == CustomPersona(
        title="Технооптимист",
        role="фаундер, объясняющий технологические решения просто",
        audience="технические руководители",
        tone="энергичный и прямой",
        forbidden="хайп, буллшит-бинго",
    )


def test_parse_custom_persona_ignores_unrelated_lines() -> None:
    text = "Что-то постороннее\nРоль: фаундер\nЕщё одна посторонняя строка"

    custom = parse_custom_persona(text)

    assert custom.role == "фаундер"


def test_parse_custom_persona_without_role_raises() -> None:
    with pytest.raises(ValueError):
        parse_custom_persona("Название: Технооптимист\nТон: энергичный")


def test_format_custom_persona_omits_absent_fields() -> None:
    custom = CustomPersona(
        title=None, role="фаундер", audience=None, tone="энергичный", forbidden=None
    )

    text = format_custom_persona(custom)

    assert text == "Роль: фаундер\nТон: энергичный"


def test_format_custom_persona_round_trips_through_parse() -> None:
    custom = CustomPersona(
        title="Технооптимист",
        role="фаундер, объясняющий технологические решения просто",
        audience="технические руководители",
        tone="энергичный и прямой",
        forbidden="хайп, буллшит-бинго",
    )

    assert parse_custom_persona(format_custom_persona(custom)) == custom


def test_serialize_then_resolve_round_trips_a_custom_persona() -> None:
    custom = CustomPersona(
        title="Технооптимист",
        role="фаундер",
        audience=None,
        tone="энергичный",
        forbidden=None,
    )

    persona, resolved = resolve_persona(serialize_custom_persona(custom))

    assert persona is None
    assert resolved == custom


def test_custom_persona_label_prefers_title_over_role() -> None:
    with_title = CustomPersona(
        title="Технооптимист", role="фаундер", audience=None, tone=None, forbidden=None
    )
    without_title = CustomPersona(
        title=None, role="фаундер", audience=None, tone=None, forbidden=None
    )

    assert custom_persona_label(with_title) == "Технооптимист"
    assert custom_persona_label(without_title) == "фаундер"
