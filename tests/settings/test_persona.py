from content_zavod.settings import (
    DEFAULT_PERSONA_KEY,
    PERSONAS,
    persona_setting_value,
    resolve_persona,
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


def test_legacy_voice_remains_supported_as_custom_data() -> None:
    persona, custom_persona = resolve_persona("технооптимист-фаундер")

    assert persona is None
    assert custom_persona == "технооптимист-фаундер"
