"""Settings: env-var configuration shared by both entrypoints (bot, worker).

Nothing here is hardcoded — every secret and connection string comes from the
process environment, loaded via `.env` locally (python-dotenv, no-op if the
file is absent) or from real environment variables in deployment. Yandex
credentials accept either a service-account API key (`YANDEX_API_KEY`, no
expiry) or a long-lived OAuth token (`YANDEX_OAUTH_TOKEN`, refreshed
internally into short-lived IAM tokens) — exactly one must be set, mirroring
`TextGenerator.with_service_account_key` / `.with_oauth_token`.

`TELEGRAM_NOTIFY_CHAT_ID` is where the bot process delivers Job results
(new Plans, regenerated Темы, ready Статьи) - the Membership allowlist (#8)
gates who may *act*, but there is no per-member chat registry yet, so
results are posted to one shared team chat rather than fanned out.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

DEFAULT_TIMEZONE = "Europe/Moscow"


class ConfigError(Exception):
    """A required setting is missing or malformed."""


@dataclass(frozen=True)
class YandexCredentials:
    folder_id: str
    api_key: str | None
    oauth_token: str | None


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str
    telegram_notify_chat_id: int
    postgres_dsn: str
    yandex: YandexCredentials
    timezone: ZoneInfo


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    """Load `Settings` from `env` (defaults to `os.environ`), loading `.env` first."""
    if env is None:
        load_dotenv()
        env = os.environ

    telegram_bot_token = _require(env, "TELEGRAM_BOT_TOKEN")
    notify_chat_id_raw = _require(env, "TELEGRAM_NOTIFY_CHAT_ID")
    try:
        telegram_notify_chat_id = int(notify_chat_id_raw)
    except ValueError as exc:
        raise ConfigError(
            f"invalid TELEGRAM_NOTIFY_CHAT_ID={notify_chat_id_raw!r}, must be an integer"
        ) from exc
    postgres_dsn = _require(env, "POSTGRES_DSN")
    folder_id = _require(env, "YANDEX_FOLDER_ID")
    api_key = env.get("YANDEX_API_KEY") or None
    oauth_token = env.get("YANDEX_OAUTH_TOKEN") or None
    if bool(api_key) == bool(oauth_token):
        raise ConfigError("exactly one of YANDEX_API_KEY or YANDEX_OAUTH_TOKEN must be set")

    timezone_name = env.get("APP_TIMEZONE", DEFAULT_TIMEZONE)
    try:
        timezone = ZoneInfo(timezone_name)
    except Exception as exc:
        raise ConfigError(f"invalid APP_TIMEZONE={timezone_name!r}") from exc

    return Settings(
        telegram_bot_token=telegram_bot_token,
        telegram_notify_chat_id=telegram_notify_chat_id,
        postgres_dsn=postgres_dsn,
        yandex=YandexCredentials(folder_id=folder_id, api_key=api_key, oauth_token=oauth_token),
        timezone=timezone,
    )


def _require(env: Mapping[str, str], name: str) -> str:
    value = env.get(name)
    if not value:
        raise ConfigError(f"missing required environment variable: {name}")
    return value
