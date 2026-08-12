from __future__ import annotations

import pytest

from content_zavod.settings import SettingsService
from content_zavod.telegram.niche_command import (
    handle_niche_command,
    handle_set_niche_command,
)


class FakeOwnerSettingsStore:
    def __init__(self, niche: str | None = None) -> None:
        self._niche = niche
        self.set_calls: list[tuple[str, str]] = []

    async def get(self, key: str) -> str | None:
        return self._niche if key == "niche" else None

    async def set(self, key: str, value: str) -> None:
        self.set_calls.append((key, value))
        self._niche = value


class FakeGateway:
    def __init__(self) -> None:
        self.sent_notices: list[tuple[int, str]] = []
        self.sent_errors: list[tuple[int, str]] = []

    async def send_notice(self, chat_id, text) -> None:
        self.sent_notices.append((chat_id, text))

    async def send_error(self, chat_id, text) -> None:
        self.sent_errors.append((chat_id, text))


@pytest.mark.asyncio
async def test_niche_command_reports_default_when_unset() -> None:
    store, gateway = FakeOwnerSettingsStore(None), FakeGateway()

    await handle_niche_command(SettingsService(store), gateway, chat_id=1)

    assert gateway.sent_notices == [(1, "Текущая Ниша: маркетинг")]


@pytest.mark.asyncio
async def test_niche_command_reports_persisted_override() -> None:
    store = FakeOwnerSettingsStore("b2b saas")
    gateway = FakeGateway()

    await handle_niche_command(SettingsService(store), gateway, chat_id=1)

    assert gateway.sent_notices == [(1, "Текущая Ниша: b2b saas")]


@pytest.mark.asyncio
async def test_set_niche_persists_and_echoes_normalized_text() -> None:
    store, gateway = FakeOwnerSettingsStore(), FakeGateway()

    await handle_set_niche_command(SettingsService(store), gateway, chat_id=1, args="  b2b saas  ")

    assert store.set_calls == [("niche", "b2b saas")]
    assert gateway.sent_notices == [(1, "Ниша изменена: b2b saas")]


@pytest.mark.asyncio
async def test_set_niche_accepts_multiline_text_as_is() -> None:
    store, gateway = FakeOwnerSettingsStore(), FakeGateway()
    multiline = "b2b saas\nдля малого бизнеса"

    await handle_set_niche_command(SettingsService(store), gateway, chat_id=1, args=multiline)

    assert store.set_calls == [("niche", multiline)]
    assert gateway.sent_notices == [(1, f"Ниша изменена: {multiline}")]


@pytest.mark.asyncio
async def test_set_niche_rejects_empty_args_without_side_effects() -> None:
    store, gateway = FakeOwnerSettingsStore(), FakeGateway()

    await handle_set_niche_command(SettingsService(store), gateway, chat_id=1, args="")

    assert store.set_calls == []
    assert len(gateway.sent_errors) == 1


@pytest.mark.asyncio
async def test_set_niche_rejects_whitespace_only_args_without_side_effects() -> None:
    store, gateway = FakeOwnerSettingsStore(), FakeGateway()

    await handle_set_niche_command(SettingsService(store), gateway, chat_id=1, args="   \n  ")

    assert store.set_calls == []
    assert len(gateway.sent_errors) == 1
