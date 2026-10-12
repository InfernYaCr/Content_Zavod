"""Профиль стиля (#134): stored as JSON, capped, and the last choice of Стиль автора wins."""

from __future__ import annotations

from content_zavod.settings import (
    STYLE_PROFILE_MAX_LENGTH,
    SettingsService,
    StyleProfile,
    persona_setting_value,
    style_profile_from_dict,
    style_profile_text,
    style_profile_to_dict,
)
from content_zavod.settings.style_profile import STYLE_TRAITS
from tests.tenancy import WS_A, WS_B

_PROFILE = StyleProfile(
    traits=(("address", "на «ты»"), ("tone", "ироничный")),
    summary="Вы пишете с иронией.",
    voice_examples=("ну что, поехали",),
    confidence="low",
    source="posts",
    corrections="без эмодзи",
)


class WorkspaceStore:
    """Keyed by Пространство, like `workspace_settings`."""

    def __init__(self) -> None:
        self.values: dict[tuple[int, str], str] = {}

    async def get(self, workspace_id: int, key: str) -> str | None:
        return self.values.get((workspace_id, key))

    async def set(self, workspace_id: int, key: str, value: str) -> None:
        self.values[(workspace_id, key)] = value


def test_round_trip_through_the_dict_form() -> None:
    assert style_profile_from_dict(style_profile_to_dict(_PROFILE)) == _PROFILE


def test_unreadable_or_empty_profiles_are_none() -> None:
    assert style_profile_from_dict(None) is None
    assert style_profile_from_dict({"traits": {"tone": "  "}, "summary": ""}) is None
    # A summary alone is no profile: it would put an empty author_style in the Персона's place.
    assert style_profile_from_dict({"summary": "Вы пишете."}) is None
    assert style_profile_from_dict({"traits": {"tone": "сухой"}}) is not None


def test_unknown_confidence_and_source_fall_back() -> None:
    profile = style_profile_from_dict(
        {"traits": {"tone": "т"}, "confidence": "100%", "source": "evil"}
    )

    assert profile is not None and (profile.confidence, profile.source) == ("medium", "articles")


def test_rendered_traits_never_exceed_the_cap() -> None:
    profile = style_profile_from_dict(
        {"traits": {key: "х" * 500 for key in STYLE_TRAITS}, "summary": "s"}
    )

    assert profile is not None
    text = style_profile_text(profile)
    assert 0 < len(text) <= STYLE_PROFILE_MAX_LENGTH
    assert text.startswith("Обращение к читателю: ")
    # Every trait still fits at its cap; what the author avoids comes early.
    assert text.count("\n") == len(STYLE_TRAITS) - 1
    assert text.index("Так не пишет") < text.index("Эмодзи")


async def test_a_saved_profile_is_read_back_only_in_its_own_workspace() -> None:
    store = WorkspaceStore()
    settings = SettingsService(store)

    await settings.set_style_profile(WS_A, _PROFILE)

    assert (await settings.read(WS_A)).style_profile == _PROFILE
    assert (await settings.read(WS_B)).style_profile is None


async def test_picking_a_preset_or_a_custom_persona_clears_the_profile() -> None:
    store = WorkspaceStore()
    settings = SettingsService(store)

    await settings.set_style_profile(WS_A, _PROFILE)
    await settings.set_persona(WS_A, persona_setting_value("clear_teacher"))
    assert (await settings.read(WS_A)).style_profile is None

    await settings.set_style_profile(WS_A, _PROFILE)
    await settings.set_persona(WS_A, "Роль: пекарь")
    assert (await settings.read(WS_A)).style_profile is None


async def test_a_corrupt_stored_profile_reads_as_none() -> None:
    store = WorkspaceStore()
    store.values[(WS_A, "style_profile")] = "{not json"

    assert (await SettingsService(store).read(WS_A)).style_profile is None


def test_a_long_trait_is_cut_on_a_word_boundary_with_an_ellipsis() -> None:
    profile = style_profile_from_dict({"traits": {"tone": "спокойный " * 30}})

    assert profile is not None
    ((_key, value),) = profile.traits
    assert len(value) <= 100 and value.endswith("…")
    assert value[:-1].split()[-1] == "спокойный"
