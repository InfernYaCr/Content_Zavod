from __future__ import annotations

from content_zavod.owner_settings import OwnerSettingsStore


async def test_get_returns_none_when_unset(owner_settings: OwnerSettingsStore) -> None:
    assert await owner_settings.get("niche") is None


async def test_set_then_get_roundtrips(owner_settings: OwnerSettingsStore) -> None:
    await owner_settings.set("niche", "b2b saas")

    assert await owner_settings.get("niche") == "b2b saas"


async def test_set_is_idempotent_upsert(owner_settings: OwnerSettingsStore) -> None:
    await owner_settings.set("niche", "b2b saas")
    await owner_settings.set("niche", "edtech")

    assert await owner_settings.get("niche") == "edtech"


async def test_keys_are_independent(owner_settings: OwnerSettingsStore) -> None:
    await owner_settings.set("niche", "b2b saas")

    assert await owner_settings.get("voice") is None


async def test_set_if_changed_reports_whether_the_value_changed(
    owner_settings: OwnerSettingsStore,
) -> None:
    assert await owner_settings.set_if_changed("onboarding", "done") is True
    assert await owner_settings.set_if_changed("onboarding", "done") is False
    assert await owner_settings.set_if_changed("onboarding", "started") is True

    assert await owner_settings.get("onboarding") == "started"
