from __future__ import annotations

import pytest

from content_zavod.settings import SettingsService
from content_zavod.telegram.directions_command import (
    handle_directions_command,
    handle_set_directions_command,
)


class FakeOwnerSettingsStore:
    def __init__(self, directions: str | None = None) -> None:
        self._directions = directions
        self.set_calls: list[tuple[str, str]] = []

    async def get(self, key: str) -> str | None:
        return self._directions if key == "directions" else None

    async def set(self, key: str, value: str) -> None:
        self.set_calls.append((key, value))
        self._directions = value


class FakeGateway:
    def __init__(self) -> None:
        self.sent_notices: list[tuple[int, str]] = []
        self.sent_errors: list[tuple[int, str]] = []

    async def send_notice(self, chat_id, text) -> None:
        self.sent_notices.append((chat_id, text))

    async def send_error(self, chat_id, text) -> None:
        self.sent_errors.append((chat_id, text))


@pytest.mark.asyncio
async def test_directions_command_reports_default_when_unset() -> None:
    settings_store, gateway = FakeOwnerSettingsStore(None), FakeGateway()

    await handle_directions_command(SettingsService(settings_store), gateway, chat_id=1)

    assert gateway.sent_notices == [
        (
            1,
            "Текущие Направления: crm для малого бизнеса, email маркетинг, "
            "таргетированная реклама, контент маркетинг, seo продвижение сайта, "
            "воронка продаж, юнит экономика, маркетинговая стратегия",
        )
    ]


@pytest.mark.asyncio
async def test_directions_command_reports_persisted_override() -> None:
    settings_store = FakeOwnerSettingsStore("edtech курсы, онлайн школа")
    gateway = FakeGateway()

    await handle_directions_command(SettingsService(settings_store), gateway, chat_id=1)

    assert gateway.sent_notices == [(1, "Текущие Направления: edtech курсы, онлайн школа")]


@pytest.mark.asyncio
async def test_set_directions_splits_trims_and_persists() -> None:
    settings_store, gateway = FakeOwnerSettingsStore(), FakeGateway()

    await handle_set_directions_command(
        SettingsService(settings_store), gateway, chat_id=1, args="  a  , b ,c"
    )

    assert settings_store.set_calls == [("directions", "a, b, c")]
    assert gateway.sent_notices == [(1, "Направления изменены: a, b, c")]


@pytest.mark.asyncio
async def test_set_directions_drops_empty_items_from_double_and_trailing_commas() -> None:
    settings_store, gateway = FakeOwnerSettingsStore(), FakeGateway()

    await handle_set_directions_command(
        SettingsService(settings_store), gateway, chat_id=1, args="a,,b,"
    )

    assert settings_store.set_calls == [("directions", "a, b")]
    assert gateway.sent_notices == [(1, "Направления изменены: a, b")]


@pytest.mark.asyncio
async def test_set_directions_keeps_duplicates() -> None:
    settings_store, gateway = FakeOwnerSettingsStore(), FakeGateway()

    await handle_set_directions_command(
        SettingsService(settings_store), gateway, chat_id=1, args="a, a, b"
    )

    assert settings_store.set_calls == [("directions", "a, a, b")]
    assert gateway.sent_notices == [(1, "Направления изменены: a, a, b")]


@pytest.mark.asyncio
async def test_set_directions_rejects_empty_args_without_side_effects() -> None:
    settings_store, gateway = FakeOwnerSettingsStore(), FakeGateway()

    await handle_set_directions_command(
        SettingsService(settings_store), gateway, chat_id=1, args=""
    )

    assert settings_store.set_calls == []
    assert len(gateway.sent_errors) == 1


@pytest.mark.asyncio
async def test_set_directions_rejects_args_that_normalize_to_empty_list() -> None:
    settings_store, gateway = FakeOwnerSettingsStore(), FakeGateway()

    await handle_set_directions_command(
        SettingsService(settings_store), gateway, chat_id=1, args=" , , "
    )

    assert settings_store.set_calls == []
    assert len(gateway.sent_errors) == 1
