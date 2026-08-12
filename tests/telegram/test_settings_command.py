from __future__ import annotations

import pytest

from content_zavod.settings import SettingsService
from content_zavod.telegram.settings_command import handle_settings_command


class FakeOwnerSettingsStore:
    def __init__(self, values: dict[str, str] | None = None) -> None:
        self._values = values or {}

    async def get(self, key: str) -> str | None:
        return self._values.get(key)

    async def set(self, key: str, value: str) -> None:
        self._values[key] = value


class FakeGateway:
    def __init__(self) -> None:
        self.sent_notices: list[tuple[int, str]] = []

    async def send_notice(self, chat_id, text) -> None:
        self.sent_notices.append((chat_id, text))


@pytest.mark.asyncio
async def test_settings_reports_all_defaults_when_unset() -> None:
    settings, gateway = SettingsService(FakeOwnerSettingsStore()), FakeGateway()

    await handle_settings_command(settings, gateway, chat_id=1)

    assert len(gateway.sent_notices) == 1
    chat_id, text = gateway.sent_notices[0]
    assert chat_id == 1
    assert "Ниша: маркетинг (подбор/регенерация Темы)" in text
    assert "Персона (аутлайн/черновик Статьи):" in text
    assert "Маркетолог-практик" in text
    assert "Направления:" in text and "(Wordstat-подбор растущих запросов)" in text


@pytest.mark.asyncio
async def test_settings_reports_persisted_overrides() -> None:
    store = FakeOwnerSettingsStore(
        {
            "niche": "b2b saas",
            "voice": "preset:evidence_analyst",
            "directions": "seo, контент-маркетинг",
        }
    )
    settings, gateway = SettingsService(store), FakeGateway()

    await handle_settings_command(settings, gateway, chat_id=1)

    _chat_id, text = gateway.sent_notices[0]
    assert "Ниша: b2b saas (подбор/регенерация Темы)" in text
    assert "Персона (аутлайн/черновик Статьи):" in text
    assert "Доказательный аналитик" in text
    assert "Направления: seo, контент-маркетинг (Wordstat-подбор растущих запросов)" in text


@pytest.mark.asyncio
async def test_settings_mixes_defaults_and_overrides() -> None:
    settings = SettingsService(FakeOwnerSettingsStore({"niche": "b2b saas"}))
    gateway = FakeGateway()

    await handle_settings_command(settings, gateway, chat_id=1)

    _chat_id, text = gateway.sent_notices[0]
    assert "Ниша: b2b saas (подбор/регенерация Темы)" in text
    assert "Персона (аутлайн/черновик Статьи):" in text
    assert "Направления:" in text and "(Wordstat-подбор растущих запросов)" in text


@pytest.mark.asyncio
async def test_settings_shows_custom_persona_by_fields_like_persona_command() -> None:
    store = FakeOwnerSettingsStore({"voice": "Роль: технооптимист-фаундер\nТон: дерзкий"})
    settings, gateway = SettingsService(store), FakeGateway()

    await handle_settings_command(settings, gateway, chat_id=1)

    _chat_id, text = gateway.sent_notices[0]
    assert "Роль: технооптимист-фаундер" in text
    assert "Тон: дерзкий" in text


@pytest.mark.asyncio
async def test_settings_reader_reads_once_per_call() -> None:
    class CountingReader:
        def __init__(self, service: SettingsService) -> None:
            self._service = service
            self.read_calls = 0

        async def read(self):
            self.read_calls += 1
            return await self._service.read()

    reader = CountingReader(SettingsService(FakeOwnerSettingsStore()))
    gateway = FakeGateway()

    await handle_settings_command(reader, gateway, chat_id=1)

    assert reader.read_calls == 1
