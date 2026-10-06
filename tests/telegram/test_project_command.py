from __future__ import annotations

import pytest

from content_zavod.settings import SettingsService
from content_zavod.telegram.project_command import (
    handle_project_command,
    handle_set_project_command,
)


class FakeOwnerSettingsStore:
    def __init__(self, values: dict[str, str] | None = None) -> None:
        self._values = values or {}
        self.set_calls: list[tuple[str, str]] = []

    async def get(self, key: str) -> str | None:
        return self._values.get(key)

    async def set(self, key: str, value: str) -> None:
        self.set_calls.append((key, value))
        self._values[key] = value


class FakeGateway:
    def __init__(self) -> None:
        self.sent_notices: list[tuple[int, str]] = []
        self.sent_errors: list[tuple[int, str]] = []

    async def send_notice(self, chat_id, text) -> None:
        self.sent_notices.append((chat_id, text))

    async def send_error(self, chat_id, text) -> None:
        self.sent_errors.append((chat_id, text))


@pytest.mark.asyncio
async def test_project_command_reports_unset() -> None:
    gateway = FakeGateway()

    await handle_project_command(SettingsService(FakeOwnerSettingsStore()), gateway, chat_id=1)

    assert gateway.sent_notices == [(1, "Текущий Проект: не задан")]


@pytest.mark.asyncio
async def test_set_project_normalizes_the_link_and_project_shows_it() -> None:
    store, gateway = FakeOwnerSettingsStore(), FakeGateway()
    settings = SettingsService(store)

    await handle_set_project_command(
        settings, gateway, chat_id=1, args="@marketing_daily Разборы кейсов по маркетингу"
    )
    await handle_project_command(settings, gateway, chat_id=1)

    expected = "https://t.me/marketing_daily — Разборы кейсов по маркетингу"
    assert gateway.sent_notices == [
        (1, f"Проект изменён: {expected}"),
        (1, f"Текущий Проект: {expected}"),
    ]
    assert gateway.sent_errors == []


@pytest.mark.asyncio
async def test_set_project_rejects_a_bad_link_in_russian_without_writing() -> None:
    store, gateway = FakeOwnerSettingsStore(), FakeGateway()

    await handle_set_project_command(
        SettingsService(store), gateway, chat_id=1, args="www.example.ru Блог о маркетинге"
    )

    assert store.set_calls == []
    assert gateway.sent_notices == []
    [(chat_id, text)] = gateway.sent_errors
    assert chat_id == 1
    assert "Ссылка не распознана" in text
    assert "/set_project <ссылка> <описание>" in text


@pytest.mark.parametrize("args", ["", "https://example.ru"])
@pytest.mark.asyncio
async def test_set_project_without_link_or_description_replies_with_usage(args) -> None:
    store, gateway = FakeOwnerSettingsStore(), FakeGateway()

    await handle_set_project_command(SettingsService(store), gateway, chat_id=1, args=args)

    assert store.set_calls == []
    assert gateway.sent_errors == [(1, "Использование: /set_project <ссылка> <описание>")]
