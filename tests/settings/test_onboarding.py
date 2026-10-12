from __future__ import annotations

import pytest

from content_zavod.settings import (
    NICHE_KEY,
    SETTING_STORE_KEYS,
    OnboardingState,
    SettingsService,
)
from tests.tenancy import WS_A


class Store:
    def __init__(self, values: dict[str, str] | None = None) -> None:
        self.values = dict(values or {})

    async def get(self, workspace_id: int, key: str) -> str | None:
        return self.values.get(key)

    async def set(self, workspace_id: int, key: str, value: str) -> None:
        self.values[key] = value

    async def set_if_changed(self, workspace_id: int, key: str, value: str) -> bool:
        changed = self.values.get(key) != value
        self.values[key] = value
        return changed


async def test_a_fresh_install_needs_onboarding() -> None:
    assert await OnboardingState(Store()).needed(WS_A) is True


@pytest.mark.parametrize("key", SETTING_STORE_KEYS)
async def test_an_install_with_any_setting_stored_does_not(key: str) -> None:
    """Existing installs that already configured something are never pushed into the wizard."""
    assert await OnboardingState(Store({key: "x"})).needed(WS_A) is False


async def test_once_begun_it_stays_needed_even_though_settings_are_now_stored() -> None:
    store = Store()
    state = OnboardingState(store)
    await state.begin(WS_A)
    await SettingsService(store).set_niche(WS_A, "фитнес")

    assert await state.needed(WS_A) is True


async def test_finish_is_true_only_the_first_time_and_ends_it() -> None:
    store = Store()
    state = OnboardingState(store)
    await state.begin(WS_A)

    assert await state.finish(WS_A) is True
    assert await state.finish(WS_A) is False
    assert await state.needed(WS_A) is False


async def test_finished_stays_finished_even_with_settings_untouched() -> None:
    """«Позже» on a fresh install: no settings stored, but the wizard must not come back."""
    state = OnboardingState(Store())
    await state.skip(WS_A)

    assert await state.needed(WS_A) is False


async def test_a_launch_after_later_is_a_real_first_launch() -> None:
    """A «🚀 Запустить» still on screen after «Позже» must start the Plan, not claim it did."""
    state = OnboardingState(Store())
    await state.begin(WS_A)
    await state.skip(WS_A)

    assert await state.finish(WS_A) is True


async def test_later_after_a_launch_does_not_re_arm_it() -> None:
    state = OnboardingState(Store())
    await state.finish(WS_A)
    await state.skip(WS_A)

    assert await state.finish(WS_A) is False
    assert await state.needed(WS_A) is False


def test_setting_store_keys_cover_the_stored_settings() -> None:
    assert NICHE_KEY in SETTING_STORE_KEYS
    assert {"niche", "audience", "voice", "project"} <= set(SETTING_STORE_KEYS)
