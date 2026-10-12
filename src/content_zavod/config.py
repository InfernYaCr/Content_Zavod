"""Settings: env-var configuration shared by both entrypoints (bot, worker).

Nothing here is hardcoded — every secret and connection string comes from the
process environment, loaded via `.env` locally (python-dotenv, no-op if the
file is absent) or from real environment variables in deployment. Yandex
credentials accept either a service-account API key (`YANDEX_API_KEY`, no
expiry) or a long-lived OAuth token (`YANDEX_OAUTH_TOKEN`, refreshed
internally into short-lived IAM tokens) — exactly one must be set, mirroring
`TextGenerator.with_service_account_key` / `.with_oauth_token`.

Every user gets their own Пространство on /start and the bot delivers each Job result to the
private chat of the Workspace that owns the Job (ADR-0015) - there is no shared chat to
configure. `GLOBAL_ADMIN_TELEGRAM_IDS` (comma-separated telegram ids, may be empty) names the
Глобальные администраторы: not a Role, just this list.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

from .yandex.image_generator import DEFAULT_IMAGE_MODEL
from .yandex.text_generator import DEFAULT_TEXT_MODEL

DEFAULT_TIMEZONE = "Europe/Moscow"


class ConfigError(Exception):
    """A required setting is missing or malformed."""


@dataclass(frozen=True)
class YandexCredentials:
    folder_id: str
    api_key: str | None
    oauth_token: str | None


@dataclass(frozen=True)
class YandexModels:
    """Model names inside the folder's URI (`gpt://<folder>/<text>`, `art://<folder>/<image>`),
    #123: explicit names, so a model's end of support is a one-line env change."""

    text: str = DEFAULT_TEXT_MODEL
    image: str = DEFAULT_IMAGE_MODEL


@dataclass(frozen=True)
class YandexPricing:
    """Per-step cost accounting (#74) needs a real price to turn tokens/images into a
    cost - unlike every other setting here, both fields are optional: with neither set,
    every generation step's `cost` stays explicitly unknown (`None`, never a silent
    `0.0`) rather than assuming a number nobody configured."""

    text_cost_per_1k_tokens: float | None
    image_cost_per_generation: float | None
    # One Yandex Search API web search request (#94) - research makes one per Тема.
    search_cost_per_request: float | None = None


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str
    postgres_dsn: str
    yandex: YandexCredentials
    yandex_pricing: YandexPricing
    timezone: ZoneInfo
    # #94: `YANDEX_WEB_SEARCH=off` runs research in "no evidence" mode without calling the
    # Search API (e.g. while the service account lacks the web search role).
    web_search_enabled: bool = True
    # #123: `YANDEX_COVERS=off` (the default) generates no covers - no `generate_cover` Job, and
    # the Хаб and the Статья card show no cover at all.
    covers_enabled: bool = False
    yandex_models: YandexModels = YandexModels()
    # Optional (#92): without it the bot creates a Telegraph account on first use and keeps
    # its token in owner_settings.
    telegraph_access_token: str | None = None
    global_admin_telegram_ids: frozenset[int] = frozenset()


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    """Load `Settings` from `env` (defaults to `os.environ`), loading `.env` first."""
    if env is None:
        load_dotenv()
        env = os.environ

    telegram_bot_token = _require(env, "TELEGRAM_BOT_TOKEN")
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
        postgres_dsn=postgres_dsn,
        yandex=YandexCredentials(folder_id=folder_id, api_key=api_key, oauth_token=oauth_token),
        yandex_pricing=YandexPricing(
            text_cost_per_1k_tokens=_optional_float(env, "YANDEX_TEXT_COST_PER_1K_TOKENS"),
            image_cost_per_generation=_optional_float(env, "YANDEX_IMAGE_COST_PER_GENERATION"),
            search_cost_per_request=_optional_float(env, "YANDEX_SEARCH_COST_PER_REQUEST"),
        ),
        timezone=timezone,
        web_search_enabled=_flag(env, "YANDEX_WEB_SEARCH", default=True),
        telegraph_access_token=env.get("TELEGRAPH_ACCESS_TOKEN") or None,
        covers_enabled=_flag(env, "YANDEX_COVERS", default=False),
        yandex_models=YandexModels(
            text=env.get("YANDEX_TEXT_MODEL") or DEFAULT_TEXT_MODEL,
            image=env.get("YANDEX_IMAGE_MODEL") or DEFAULT_IMAGE_MODEL,
        ),
        global_admin_telegram_ids=_telegram_ids(env, "GLOBAL_ADMIN_TELEGRAM_IDS"),
    )


def _require(env: Mapping[str, str], name: str) -> str:
    value = env.get(name)
    if not value:
        raise ConfigError(f"missing required environment variable: {name}")
    return value


def _telegram_ids(env: Mapping[str, str], name: str) -> frozenset[int]:
    try:
        return frozenset(int(part) for part in (env.get(name) or "").split(",") if part.strip())
    except ValueError as exc:
        raise ConfigError(f"invalid {name}={env.get(name)!r}, must be comma-separated ids") from exc


def _flag(env: Mapping[str, str], name: str, *, default: bool) -> bool:
    value = (env.get(name) or "").strip().lower()
    if not value:
        return default
    if value in ("1", "on", "true", "yes"):
        return True
    if value in ("0", "off", "false", "no"):
        return False
    raise ConfigError(f"invalid {name}={value!r}, must be on/off")


def _optional_float(env: Mapping[str, str], name: str) -> float | None:
    value = env.get(name)
    if not value:
        return None
    try:
        return float(value)
    except ValueError as exc:
        raise ConfigError(f"invalid {name}={value!r}, must be a number") from exc
