from __future__ import annotations

import pytest

from content_zavod.domain.errors import InvalidSettingValue
from content_zavod.settings import (
    AUDIENCE_MAX_LENGTH,
    PERSONAS,
    CustomPersona,
    Project,
    SettingsService,
    normalize_project_url,
    persona_setting_value,
)
from tests.tenancy import WS_A


class InMemoryStore:
    """A generic key-value store, deliberately ignorant of what keys
    `SettingsService` uses internally or how many it reads per `read()` -
    these tests assert on `SettingsService` behavior only, never on the
    table layout underneath it."""

    def __init__(self, values: dict[str, str] | None = None) -> None:
        self._values = dict(values or {})

    async def get(self, workspace_id: int, key: str) -> str | None:
        return self._values.get(key)

    async def set(self, workspace_id: int, key: str, value: str) -> None:
        self._values[key] = value


async def test_read_applies_defaults_when_nothing_was_ever_set() -> None:
    settings = SettingsService(InMemoryStore())

    current = await settings.read(WS_A)

    assert current.niche == "маркетинг"
    # #128: Направления are gone - the Карта Ниши is built from the Ниша.
    assert not hasattr(current, "directions")


async def test_set_niche_then_read_returns_the_new_value() -> None:
    settings = SettingsService(InMemoryStore())

    await settings.set_niche(WS_A, "b2b saas")

    assert (await settings.read(WS_A)).niche == "b2b saas"


async def test_set_niche_rejects_empty_input_without_writing() -> None:
    store = InMemoryStore()
    settings = SettingsService(store)

    with pytest.raises(InvalidSettingValue):
        await settings.set_niche(WS_A, "   ")

    current = await settings.read(WS_A)
    assert current.niche == "маркетинг"


async def test_missing_row_means_no_override_not_a_migration_needed() -> None:
    store = InMemoryStore()

    await SettingsService(store).set_niche(WS_A, "edtech")
    same_store_reader = await SettingsService(store).read(WS_A)
    fresh_store_reader = await SettingsService(InMemoryStore()).read(WS_A)

    assert same_store_reader.niche == "edtech"
    assert fresh_store_reader.niche == "маркетинг"


async def test_set_persona_via_preset_marker_stores_it_as_is() -> None:
    settings = SettingsService(InMemoryStore())

    value = persona_setting_value("evidence_analyst")
    await settings.set_persona(WS_A, value)
    current = await settings.read(WS_A)

    assert current.persona == PERSONAS["evidence_analyst"]
    assert current.custom_persona is None


async def test_set_persona_with_marked_lines_saves_five_fields() -> None:
    settings = SettingsService(InMemoryStore())
    text = (
        "Название: Технооптимист\n"
        "Роль: фаундер, объясняющий технологические решения просто\n"
        "Аудитория: технические руководители\n"
        "Тон: энергичный и прямой\n"
        "Запрещено: хайп, буллшит-бинго"
    )

    await settings.set_persona(WS_A, text)
    current = await settings.read(WS_A)

    assert current.persona is None
    assert current.custom_persona == CustomPersona(
        title="Технооптимист",
        role="фаундер, объясняющий технологические решения просто",
        audience="технические руководители",
        tone="энергичный и прямой",
        forbidden="хайп, буллшит-бинго",
    )


async def test_set_persona_omits_unfilled_fields_rather_than_defaulting_them() -> None:
    settings = SettingsService(InMemoryStore())

    await settings.set_persona(WS_A, "Роль: фаундер")
    current = await settings.read(WS_A)

    assert current.custom_persona == CustomPersona(
        title=None, role="фаундер", audience=None, tone=None, forbidden=None
    )


async def test_set_persona_without_role_rejects_input_without_writing() -> None:
    store = InMemoryStore()
    settings = SettingsService(store)

    with pytest.raises(InvalidSettingValue):
        await settings.set_persona(WS_A, "Название: Технооптимист\nТон: энергичный")

    current = await settings.read(WS_A)
    assert current.persona is not None
    assert current.persona.key == "practical_marketer"


async def test_set_persona_rejects_empty_input_without_writing() -> None:
    settings = SettingsService(InMemoryStore())

    with pytest.raises(InvalidSettingValue):
        await settings.set_persona(WS_A, "   ")


async def test_project_is_unset_by_default() -> None:
    current = await SettingsService(InMemoryStore()).read(WS_A)

    assert current.project is None


@pytest.mark.parametrize(
    ("link", "expected_url"),
    [
        ("@marketing_daily", "https://t.me/marketing_daily"),
        ("t.me/marketing_daily", "https://t.me/marketing_daily"),
        ("T.me/marketing_daily", "https://t.me/marketing_daily"),
        ("telegram.me/marketing_daily", "https://t.me/marketing_daily"),
        ("https://t.me/marketing_daily", "https://t.me/marketing_daily"),
        ("http://www.t.me/marketing_daily/", "https://t.me/marketing_daily"),
        ("https://telegram.me/marketing_daily", "https://t.me/marketing_daily"),
        ("t.me/s/marketing_daily", "https://t.me/marketing_daily"),
        ("https://t.me/s/marketing_daily", "https://t.me/marketing_daily"),
        ("<https://t.me/marketing_daily>", "https://t.me/marketing_daily"),
        ("«@marketing_daily»", "https://t.me/marketing_daily"),
        ("https://example.ru/blog", "https://example.ru/blog"),
        ("Https://example.ru", "https://example.ru"),
        ("example.ru", "https://example.ru"),
        ("www.example.ru/path", "https://www.example.ru/path"),
        ("http://example.ru", "https://example.ru"),
        ("https://Example.RU/Blog/", "https://example.ru/Blog"),
        ("https://example.ru/", "https://example.ru"),
        ("https://example.ru/blog?id=1", "https://example.ru/blog?id=1"),
        ('"example.ru".', "https://example.ru"),
        ("example.ru),", "https://example.ru"),
        ("сайт.рф", "https://сайт.рф"),
        ("https://Сайт.РФ/блог", "https://сайт.рф/блог"),
        ("example.ru//", "https://example.ru"),
        ("https://example.ru/blog//", "https://example.ru/blog"),
        ("t.me/marketing_daily//", "https://t.me/marketing_daily"),
        ("ｅxample.ru", "https://example.ru"),
        ("https://ＥＸＡＭＰＬＥ．ru/blog", "https://example.ru/blog"),
    ],
)
async def test_set_project_normalizes_the_link_and_round_trips(link, expected_url) -> None:
    settings = SettingsService(InMemoryStore())

    saved = await settings.set_project(WS_A, f"{link}  Разборы кейсов\nпо маркетингу ")
    current = await settings.read(WS_A)

    assert saved == Project(url=expected_url, description="Разборы кейсов\nпо маркетингу")
    assert current.project == saved
    assert normalize_project_url(expected_url) == expected_url  # idempotent


@pytest.mark.parametrize(
    "link",
    [
        "Канал",
        "example",
        "1.5",
        "@abc",
        "@1channel",
        "https://",
        "t.me/",
        "t.me/s/",
        "https://t.me",
        "ftp://example.ru",
        "javascript:alert(1)",
        "https://user@example.ru",
        "https://example.ru:8080",
    ],
)
async def test_set_project_rejects_an_unusable_link_without_writing(link) -> None:
    store = InMemoryStore()

    with pytest.raises(InvalidSettingValue) as excinfo:
        await SettingsService(store).set_project(WS_A, f"{link} Канал о маркетинге")

    assert excinfo.value.field == "project_url"
    assert (await SettingsService(store).read(WS_A)).project is None


@pytest.mark.parametrize("value", ["", "   "])
async def test_set_project_rejects_empty_input(value) -> None:
    store = InMemoryStore()

    with pytest.raises(InvalidSettingValue) as excinfo:
        await SettingsService(store).set_project(WS_A, value)

    assert excinfo.value.field == "project"
    assert (await SettingsService(store).read(WS_A)).project is None


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("@marketing_daily", Project(url="https://t.me/marketing_daily", description="")),
        ("example.ru  ", Project(url="https://example.ru", description="")),
    ],
)
async def test_set_project_saves_a_link_without_description_and_reads_it_back(
    value, expected
) -> None:
    """#133: the description is optional - a lone link must survive the store round-trip."""
    settings = SettingsService(InMemoryStore())

    assert await settings.set_project(WS_A, value) == expected
    assert (await settings.read(WS_A)).project == expected


async def test_project_stored_before_133_still_reads_with_its_description() -> None:
    store = InMemoryStore({"project": "https://t.me/channel Наш канал"})

    project = (await SettingsService(store).read(WS_A)).project

    assert project == Project(url="https://t.me/channel", description="Наш канал")


@pytest.mark.parametrize(
    ("project", "kind", "label"),
    [
        (Project(url="https://t.me/channel"), "telegram", "Telegram-канал автора"),
        (Project(url="https://example.ru"), "site", "сайт автора"),
        (Project(url="https://t.me/channel", description="Наш канал"), "telegram", "Наш канал"),
    ],
)
def test_project_label_is_derived_when_there_is_no_description(project, kind, label) -> None:
    assert project.kind == kind
    assert project.label == label


async def test_set_project_dash_removes_the_project() -> None:
    settings = SettingsService(InMemoryStore())
    await settings.set_project(WS_A, "@marketing_daily Разборы кейсов")

    assert await settings.set_project(WS_A, " - ") is None
    assert (await settings.read(WS_A)).project is None


async def test_audience_is_unset_by_default() -> None:
    current = await SettingsService(InMemoryStore()).read(WS_A)

    assert current.audience is None


async def test_set_audience_strips_and_round_trips_keeping_line_breaks() -> None:
    settings = SettingsService(InMemoryStore())

    saved = await settings.set_audience(
        WS_A, "  Владельцы кофеен.\nБоль — нет времени на маркетинг.  "
    )

    assert saved == "Владельцы кофеен.\nБоль — нет времени на маркетинг."
    assert (await settings.read(WS_A)).audience == saved


@pytest.mark.parametrize("value", ["", "   ", "\n\t"])
async def test_set_audience_rejects_empty_input_without_writing(value) -> None:
    store = InMemoryStore()

    with pytest.raises(InvalidSettingValue) as excinfo:
        await SettingsService(store).set_audience(WS_A, value)

    assert excinfo.value.field == "audience"
    assert (await SettingsService(store).read(WS_A)).audience is None


async def test_set_audience_rejects_an_overlong_portrait_without_writing() -> None:
    store = InMemoryStore()

    with pytest.raises(InvalidSettingValue) as excinfo:
        await SettingsService(store).set_audience(WS_A, "я" * (AUDIENCE_MAX_LENGTH + 1))

    assert excinfo.value.field == "audience_too_long"
    assert (await SettingsService(store).read(WS_A)).audience is None


async def test_set_audience_dash_removes_the_audience() -> None:
    settings = SettingsService(InMemoryStore())
    await settings.set_audience(WS_A, "Владельцы кофеен")

    assert await settings.set_audience(WS_A, " - ") is None
    assert (await settings.read(WS_A)).audience is None
