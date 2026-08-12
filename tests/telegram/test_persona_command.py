from __future__ import annotations

import pytest

from content_zavod.settings import SettingsService
from content_zavod.telegram.persona_command import (
    PERSONA_TEMPLATES,
    handle_persona_command,
    handle_persona_template_callback,
    handle_set_persona_command,
)


class FakeOwnerSettingsStore:
    def __init__(self, persona: str | None = None) -> None:
        self._persona = persona
        self.set_calls: list[tuple[str, str]] = []

    async def get(self, key: str) -> str | None:
        return self._persona if key == "voice" else None

    async def set(self, key: str, value: str) -> None:
        self.set_calls.append((key, value))
        self._persona = value


class FakeGateway:
    def __init__(self) -> None:
        self.sent_notices: list[tuple[int, str, object]] = []
        self.sent_errors: list[tuple[int, str]] = []

    async def send_notice(self, chat_id, text, reply_markup=None) -> None:
        self.sent_notices.append((chat_id, text, reply_markup))

    async def send_error(self, chat_id, text) -> None:
        self.sent_errors.append((chat_id, text))


@pytest.mark.asyncio
async def test_persona_command_reports_default_when_unset() -> None:
    store, gateway = FakeOwnerSettingsStore(None), FakeGateway()

    await handle_persona_command(SettingsService(store), gateway, chat_id=1)

    assert len(gateway.sent_notices) == 1
    chat_id, text, reply_markup = gateway.sent_notices[0]
    assert (chat_id, text) == (1, "Текущая Персона: Маркетолог-практик")
    assert reply_markup is not None


@pytest.mark.asyncio
async def test_persona_command_reports_persisted_custom_override() -> None:
    store = FakeOwnerSettingsStore("технооптимист-фаундер")
    gateway = FakeGateway()

    await handle_persona_command(SettingsService(store), gateway, chat_id=1)

    chat_id, text, _reply_markup = gateway.sent_notices[0]
    assert (chat_id, text) == (1, "Текущая Персона: технооптимист-фаундер")


@pytest.mark.asyncio
async def test_persona_command_offers_preset_buttons() -> None:
    store, gateway = FakeOwnerSettingsStore(None), FakeGateway()

    await handle_persona_command(SettingsService(store), gateway, chat_id=1)

    _chat_id, _text, reply_markup = gateway.sent_notices[0]
    button_texts = [button.text for row in reply_markup.inline_keyboard for button in row]
    assert button_texts == [title for title, _value in PERSONA_TEMPLATES]


@pytest.mark.asyncio
async def test_persona_template_callback_saves_the_same_value_as_the_set_persona_path() -> None:
    store, gateway = FakeOwnerSettingsStore(), FakeGateway()
    template_index = 1
    _title, template_value = PERSONA_TEMPLATES[template_index]

    await handle_persona_template_callback(
        SettingsService(store), gateway, chat_id=1, template_index=template_index
    )

    assert store.set_calls == [("voice", template_value)]

    await handle_persona_command(SettingsService(store), gateway, chat_id=1)
    _chat_id, text, _reply_markup = gateway.sent_notices[-1]
    assert text == f"Текущая Персона: {PERSONA_TEMPLATES[template_index][0]}"


@pytest.mark.asyncio
async def test_set_persona_persists_and_echoes_normalized_custom_text() -> None:
    store, gateway = FakeOwnerSettingsStore(), FakeGateway()

    await handle_set_persona_command(
        SettingsService(store), gateway, chat_id=1, args="  технооптимист-фаундер  "
    )

    assert store.set_calls == [("voice", "технооптимист-фаундер")]
    assert gateway.sent_notices == [(1, "Персона изменена: технооптимист-фаундер", None)]


@pytest.mark.asyncio
async def test_set_persona_accepts_multiline_text_as_is() -> None:
    store, gateway = FakeOwnerSettingsStore(), FakeGateway()
    multiline = "технооптимист-фаундер\nпишет прямо и по делу"

    await handle_set_persona_command(SettingsService(store), gateway, chat_id=1, args=multiline)

    assert store.set_calls == [("voice", multiline)]
    assert gateway.sent_notices == [(1, f"Персона изменена: {multiline}", None)]


@pytest.mark.asyncio
async def test_set_persona_rejects_empty_args_without_side_effects() -> None:
    store, gateway = FakeOwnerSettingsStore(), FakeGateway()

    await handle_set_persona_command(SettingsService(store), gateway, chat_id=1, args="")

    assert store.set_calls == []
    assert len(gateway.sent_errors) == 1


@pytest.mark.asyncio
async def test_set_persona_rejects_whitespace_only_args_without_side_effects() -> None:
    store, gateway = FakeOwnerSettingsStore(), FakeGateway()

    await handle_set_persona_command(SettingsService(store), gateway, chat_id=1, args="   \n  ")

    assert store.set_calls == []
    assert len(gateway.sent_errors) == 1


@pytest.mark.asyncio
async def test_set_persona_via_preset_marker_saves_exactly_the_same_as_manual_entry() -> None:
    """AC: picking a Preset button must save identically to typing its stored value by hand."""
    store_via_button, store_via_typing = FakeOwnerSettingsStore(), FakeOwnerSettingsStore()
    gateway = FakeGateway()
    template_index = 0
    _title, preset_value = PERSONA_TEMPLATES[template_index]

    await handle_persona_template_callback(
        SettingsService(store_via_button), gateway, chat_id=1, template_index=template_index
    )
    await handle_set_persona_command(
        SettingsService(store_via_typing), gateway, chat_id=1, args=preset_value
    )

    assert store_via_button.set_calls == store_via_typing.set_calls
