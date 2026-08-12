from __future__ import annotations

import pytest

from content_zavod.domain.errors import InvalidSettingValue
from content_zavod.settings import SettingsService


class InMemoryStore:
    """A generic key-value store, deliberately ignorant of what keys
    `SettingsService` uses internally or how many it reads per `read()` -
    these tests assert on `SettingsService` behavior only, never on the
    table layout underneath it."""

    def __init__(self, values: dict[str, str] | None = None) -> None:
        self._values = dict(values or {})

    async def get(self, key: str) -> str | None:
        return self._values.get(key)

    async def set(self, key: str, value: str) -> None:
        self._values[key] = value


async def test_read_applies_defaults_when_nothing_was_ever_set() -> None:
    settings = SettingsService(InMemoryStore())

    current = await settings.read()

    assert current.niche == "маркетинг"
    assert len(current.directions) > 0


async def test_set_niche_then_read_returns_the_new_value_independent_of_directions() -> None:
    settings = SettingsService(InMemoryStore())

    await settings.set_niche("b2b saas")
    current = await settings.read()

    assert current.niche == "b2b saas"
    assert current.directions == (await SettingsService(InMemoryStore()).read()).directions


async def test_set_directions_then_read_returns_the_new_value_independent_of_niche() -> None:
    settings = SettingsService(InMemoryStore())

    await settings.set_directions("a, b, c")
    current = await settings.read()

    assert current.directions == ("a", "b", "c")
    assert current.niche == "маркетинг"


async def test_directions_parsing_drops_empties_trims_and_keeps_duplicates() -> None:
    settings = SettingsService(InMemoryStore())

    await settings.set_directions("  a ,, b ,a,")
    current = await settings.read()

    assert current.directions == ("a", "b", "a")


async def test_set_niche_rejects_empty_input_without_writing() -> None:
    store = InMemoryStore()
    settings = SettingsService(store)

    with pytest.raises(InvalidSettingValue):
        await settings.set_niche("   ")

    current = await settings.read()
    assert current.niche == "маркетинг"


async def test_set_directions_rejects_input_that_normalizes_to_empty_without_writing() -> None:
    settings = SettingsService(InMemoryStore())

    with pytest.raises(InvalidSettingValue):
        await settings.set_directions(" , , ")

    current = await settings.read()
    assert len(current.directions) > 0


async def test_missing_row_means_no_override_not_a_migration_needed() -> None:
    store = InMemoryStore()

    await SettingsService(store).set_niche("edtech")
    same_store_reader = await SettingsService(store).read()
    fresh_store_reader = await SettingsService(InMemoryStore()).read()

    assert same_store_reader.niche == "edtech"
    assert fresh_store_reader.niche == "маркетинг"
