from __future__ import annotations

import pytest

from content_zavod.telegram.voice_command import (
    VOICE_TEMPLATES,
    handle_set_voice_command,
    handle_voice_command,
    handle_voice_template_callback,
)


class FakeOwnerSettingsStore:
    def __init__(self, voice: str | None = None) -> None:
        self._voice = voice
        self.set_calls: list[tuple[str, str]] = []

    async def get(self, key: str) -> str | None:
        assert key == "voice"
        return self._voice

    async def set(self, key: str, value: str) -> None:
        self.set_calls.append((key, value))
        self._voice = value


class FakeGateway:
    def __init__(self) -> None:
        self.sent_notices: list[tuple[int, str, object]] = []
        self.sent_errors: list[tuple[int, str]] = []

    async def send_notice(self, chat_id, text, reply_markup=None) -> None:
        self.sent_notices.append((chat_id, text, reply_markup))

    async def send_error(self, chat_id, text) -> None:
        self.sent_errors.append((chat_id, text))


@pytest.mark.asyncio
async def test_voice_command_reports_default_when_unset() -> None:
    settings_store, gateway = FakeOwnerSettingsStore(None), FakeGateway()

    await handle_voice_command(settings_store, gateway, chat_id=1)

    assert len(gateway.sent_notices) == 1
    chat_id, text, reply_markup = gateway.sent_notices[0]
    assert (chat_id, text) == (1, "Текущий Голос: маркетолог-практик")
    assert reply_markup is not None


@pytest.mark.asyncio
async def test_voice_command_reports_persisted_override() -> None:
    settings_store = FakeOwnerSettingsStore("технооптимист-фаундер")
    gateway = FakeGateway()

    await handle_voice_command(settings_store, gateway, chat_id=1)

    chat_id, text, _reply_markup = gateway.sent_notices[0]
    assert (chat_id, text) == (1, "Текущий Голос: технооптимист-фаундер")


@pytest.mark.asyncio
async def test_voice_command_offers_persona_template_buttons() -> None:
    settings_store, gateway = FakeOwnerSettingsStore(None), FakeGateway()

    await handle_voice_command(settings_store, gateway, chat_id=1)

    _chat_id, _text, reply_markup = gateway.sent_notices[0]
    button_texts = [button.text for row in reply_markup.inline_keyboard for button in row]
    assert button_texts == [title for title, _template_text in VOICE_TEMPLATES]


@pytest.mark.asyncio
async def test_voice_template_callback_saves_template_text_via_set_voice_path() -> None:
    settings_store, gateway = FakeOwnerSettingsStore(), FakeGateway()
    template_index = 1
    _title, template_text = VOICE_TEMPLATES[template_index]

    await handle_voice_template_callback(
        settings_store, gateway, chat_id=1, template_index=template_index
    )

    assert settings_store.set_calls == [("voice", template_text)]

    await handle_voice_command(settings_store, gateway, chat_id=1)
    _chat_id, text, _reply_markup = gateway.sent_notices[-1]
    assert text == f"Текущий Голос: {VOICE_TEMPLATES[template_index][0]}"


@pytest.mark.asyncio
async def test_set_voice_persists_and_echoes_normalized_text() -> None:
    settings_store, gateway = FakeOwnerSettingsStore(), FakeGateway()

    await handle_set_voice_command(
        settings_store, gateway, chat_id=1, args="  технооптимист-фаундер  "
    )

    assert settings_store.set_calls == [("voice", "технооптимист-фаундер")]
    assert gateway.sent_notices == [(1, "Голос изменён: технооптимист-фаундер", None)]


@pytest.mark.asyncio
async def test_set_voice_accepts_multiline_text_as_is() -> None:
    settings_store, gateway = FakeOwnerSettingsStore(), FakeGateway()
    multiline = "технооптимист-фаундер\nпишет прямо и по делу"

    await handle_set_voice_command(settings_store, gateway, chat_id=1, args=multiline)

    assert settings_store.set_calls == [("voice", multiline)]
    assert gateway.sent_notices == [(1, f"Голос изменён: {multiline}", None)]


@pytest.mark.asyncio
async def test_set_voice_rejects_empty_args_without_side_effects() -> None:
    settings_store, gateway = FakeOwnerSettingsStore(), FakeGateway()

    await handle_set_voice_command(settings_store, gateway, chat_id=1, args="")

    assert settings_store.set_calls == []
    assert len(gateway.sent_errors) == 1


@pytest.mark.asyncio
async def test_set_voice_rejects_whitespace_only_args_without_side_effects() -> None:
    settings_store, gateway = FakeOwnerSettingsStore(), FakeGateway()

    await handle_set_voice_command(settings_store, gateway, chat_id=1, args="   \n  ")

    assert settings_store.set_calls == []
    assert len(gateway.sent_errors) == 1
