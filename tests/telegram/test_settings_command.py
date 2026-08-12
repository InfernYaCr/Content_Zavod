from __future__ import annotations

import pytest

from content_zavod.telegram.settings_command import handle_settings_command


class FakeOwnerSettingsStore:
    def __init__(self, values: dict[str, str] | None = None) -> None:
        self._values = values or {}

    async def get(self, key: str) -> str | None:
        return self._values.get(key)


class FakeGateway:
    def __init__(self) -> None:
        self.sent_notices: list[tuple[int, str]] = []

    async def send_notice(self, chat_id, text) -> None:
        self.sent_notices.append((chat_id, text))


@pytest.mark.asyncio
async def test_settings_reports_all_defaults_when_unset() -> None:
    settings_store, gateway = FakeOwnerSettingsStore(), FakeGateway()

    await handle_settings_command(settings_store, gateway, chat_id=1)

    assert len(gateway.sent_notices) == 1
    chat_id, text = gateway.sent_notices[0]
    assert chat_id == 1
    assert "Ниша: маркетинг (подбор/регенерация Темы)" in text
    assert "Персона:" in text and "(аутлайн/черновик Статьи)" in text
    assert "Направления:" in text and "(Wordstat-подбор растущих запросов)" in text


@pytest.mark.asyncio
async def test_settings_reports_persisted_overrides() -> None:
    settings_store = FakeOwnerSettingsStore(
        {
            "niche": "b2b saas",
            "voice": "экспертный, без воды",
            "directions": "seo, контент-маркетинг",
        }
    )
    gateway = FakeGateway()

    await handle_settings_command(settings_store, gateway, chat_id=1)

    _chat_id, text = gateway.sent_notices[0]
    assert "Ниша: b2b saas (подбор/регенерация Темы)" in text
    assert "Персона: экспертный, без воды (аутлайн/черновик Статьи)" in text
    assert "Направления: seo, контент-маркетинг (Wordstat-подбор растущих запросов)" in text


@pytest.mark.asyncio
async def test_settings_mixes_defaults_and_overrides() -> None:
    settings_store = FakeOwnerSettingsStore({"niche": "b2b saas"})
    gateway = FakeGateway()

    await handle_settings_command(settings_store, gateway, chat_id=1)

    _chat_id, text = gateway.sent_notices[0]
    assert "Ниша: b2b saas (подбор/регенерация Темы)" in text
    assert "Персона:" in text and "(аутлайн/черновик Статьи)" in text
    assert "Направления:" in text and "(Wordstat-подбор растущих запросов)" in text
