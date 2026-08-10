import pytest

from content_zavod.config import ConfigError, load_settings

_BASE_ENV = {
    "TELEGRAM_BOT_TOKEN": "123:abc",
    "TELEGRAM_NOTIFY_CHAT_ID": "42",
    "POSTGRES_DSN": "postgresql://localhost/content_zavod",
    "YANDEX_FOLDER_ID": "folder-1",
    "YANDEX_API_KEY": "api-key",
}


def test_load_settings_with_api_key() -> None:
    settings = load_settings(_BASE_ENV)

    assert settings.telegram_bot_token == "123:abc"
    assert settings.telegram_notify_chat_id == 42
    assert settings.postgres_dsn == "postgresql://localhost/content_zavod"
    assert settings.yandex.folder_id == "folder-1"
    assert settings.yandex.api_key == "api-key"
    assert settings.yandex.oauth_token is None
    assert str(settings.timezone) == "Europe/Moscow"


def test_load_settings_with_oauth_token() -> None:
    env = {**_BASE_ENV, "YANDEX_API_KEY": ""}
    env["YANDEX_OAUTH_TOKEN"] = "oauth-token"

    settings = load_settings(env)

    assert settings.yandex.api_key is None
    assert settings.yandex.oauth_token == "oauth-token"


def test_load_settings_uses_custom_timezone() -> None:
    env = {**_BASE_ENV, "APP_TIMEZONE": "UTC"}

    settings = load_settings(env)

    assert str(settings.timezone) == "UTC"


@pytest.mark.parametrize(
    "missing", ["TELEGRAM_BOT_TOKEN", "TELEGRAM_NOTIFY_CHAT_ID", "POSTGRES_DSN", "YANDEX_FOLDER_ID"]
)
def test_load_settings_raises_for_missing_required_var(missing: str) -> None:
    env = dict(_BASE_ENV)
    del env[missing]

    with pytest.raises(ConfigError):
        load_settings(env)


def test_load_settings_raises_when_no_yandex_credential_is_set() -> None:
    env = {**_BASE_ENV, "YANDEX_API_KEY": ""}

    with pytest.raises(ConfigError):
        load_settings(env)


def test_load_settings_raises_when_both_yandex_credentials_are_set() -> None:
    env = {**_BASE_ENV, "YANDEX_OAUTH_TOKEN": "oauth-token"}

    with pytest.raises(ConfigError):
        load_settings(env)


def test_load_settings_raises_for_non_integer_notify_chat_id() -> None:
    env = {**_BASE_ENV, "TELEGRAM_NOTIFY_CHAT_ID": "not-a-number"}

    with pytest.raises(ConfigError):
        load_settings(env)


def test_load_settings_raises_for_invalid_timezone() -> None:
    env = {**_BASE_ENV, "APP_TIMEZONE": "Not/A_Zone"}

    with pytest.raises(ConfigError):
        load_settings(env)
