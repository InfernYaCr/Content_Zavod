from __future__ import annotations

import asyncpg

from content_zavod.owner_settings import BotStateStore, WorkspaceSettingsStore
from content_zavod.settings import OnboardingState
from tests.tenancy import WS_A, WS_B


async def test_get_returns_none_when_unset(owner_settings: WorkspaceSettingsStore) -> None:
    assert await owner_settings.get(WS_A, "niche") is None


async def test_set_then_get_roundtrips(owner_settings: WorkspaceSettingsStore) -> None:
    await owner_settings.set(WS_A, "niche", "b2b saas")

    assert await owner_settings.get(WS_A, "niche") == "b2b saas"


async def test_set_is_idempotent_upsert(owner_settings: WorkspaceSettingsStore) -> None:
    await owner_settings.set(WS_A, "niche", "b2b saas")
    await owner_settings.set(WS_A, "niche", "edtech")

    assert await owner_settings.get(WS_A, "niche") == "edtech"


async def test_keys_are_independent(owner_settings: WorkspaceSettingsStore) -> None:
    await owner_settings.set(WS_A, "niche", "b2b saas")

    assert await owner_settings.get(WS_A, "voice") is None


async def test_set_if_changed_reports_whether_the_value_changed(
    owner_settings: WorkspaceSettingsStore,
) -> None:
    assert await owner_settings.set_if_changed(WS_A, "onboarding", "done") is True
    assert await owner_settings.set_if_changed(WS_A, "onboarding", "done") is False
    assert await owner_settings.set_if_changed(WS_A, "onboarding", "started") is True

    assert await owner_settings.get(WS_A, "onboarding") == "started"


async def test_a_workspaces_settings_are_invisible_to_another(
    owner_settings: WorkspaceSettingsStore,
) -> None:
    await owner_settings.set(WS_A, "niche", "кофейни")
    await owner_settings.set(WS_B, "niche", "стоматология")

    assert await owner_settings.get(WS_A, "niche") == "кофейни"
    assert await owner_settings.get(WS_B, "niche") == "стоматология"
    assert await owner_settings.get(WS_B, "audience") is None


async def test_onboarding_state_is_per_workspace(owner_settings: WorkspaceSettingsStore) -> None:
    state = OnboardingState(owner_settings)

    await state.begin(WS_A)
    assert await state.finish(WS_A) is True

    assert await state.needed(WS_A) is False
    assert await state.needed(WS_B) is True
    assert await state.finish(WS_B) is True


async def test_bot_state_is_one_store_for_the_whole_bot(pool: asyncpg.Pool) -> None:
    store = BotStateStore(pool)

    await store.set("telegraph_access_token", "t-1")
    await store.set("telegraph_access_token", "t-2")

    assert await store.get("telegraph_access_token") == "t-2"
    assert await store.get("asset_file_id:welcome") is None
