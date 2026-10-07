from __future__ import annotations

import pytest

from content_zavod.settings import (
    NICHE_KEY,
    SETTING_STORE_KEYS,
    OnboardingState,
    SettingsService,
)


class Store:
    def __init__(self, values: dict[str, str] | None = None) -> None:
        self.values = dict(values or {})

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def set(self, key: str, value: str) -> None:
        self.values[key] = value

    async def set_if_changed(self, key: str, value: str) -> bool:
        changed = self.values.get(key) != value
        self.values[key] = value
        return changed


async def test_a_fresh_install_needs_onboarding() -> None:
    assert await OnboardingState(Store()).needed() is True


@pytest.mark.parametrize("key", SETTING_STORE_KEYS)
async def test_an_install_with_any_setting_stored_does_not(key: str) -> None:
    """Existing installs that already configured something are never pushed into the wizard."""
    assert await OnboardingState(Store({key: "x"})).needed() is False


async def test_once_begun_it_stays_needed_even_though_settings_are_now_stored() -> None:
    store = Store()
    state = OnboardingState(store)
    await state.begin()
    await SettingsService(store).set_niche("фитнес")

    assert await state.needed() is True


async def test_finish_is_true_only_the_first_time_and_ends_it() -> None:
    store = Store()
    state = OnboardingState(store)
    await state.begin()

    assert await state.finish() is True
    assert await state.finish() is False
    assert await state.needed() is False


async def test_finished_stays_finished_even_with_settings_untouched() -> None:
    """«Позже» on a fresh install: no settings stored, but the wizard must not come back."""
    state = OnboardingState(Store())
    await state.finish()

    assert await state.needed() is False


def test_setting_store_keys_cover_the_stored_settings() -> None:
    assert NICHE_KEY in SETTING_STORE_KEYS
    assert {"niche", "audience", "directions", "voice", "project"} <= set(SETTING_STORE_KEYS)
