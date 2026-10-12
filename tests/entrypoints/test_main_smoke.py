"""Smoke tests for the bot and worker `main()`: all the wiring is built against a real,
empty Postgres and the process reaches its main loop (#125). #122 - `main()` passing a
kwarg the callee no longer took - crashed the bot at start while CI stayed green.

Only the network edges are faked: Telegram API calls, aiogram polling, and SIGTERM (the
stop Event is set as soon as it is registered, so each loop exits on its first check).
"""

from __future__ import annotations

import asyncio
from typing import Any

import asyncpg
import pytest
from aiogram import Bot, Dispatcher
from aiogram.methods import GetMe
from aiogram.types import User
from testcontainers.community.postgres import PostgresContainer

from content_zavod.config import Settings, load_settings
from content_zavod.entrypoints import bot as bot_entrypoint
from content_zavod.entrypoints import worker as worker_entrypoint


async def _settings_on_fresh_db(container: PostgresContainer, name: str) -> Settings:
    """Settings whose POSTGRES_DSN is a new database, so `main()` runs every migration from
    scratch and whatever it enqueues stays out of the shared test database."""
    host, port = container.get_container_host_ip(), container.get_exposed_port(5432)
    credentials = f"{container.username}:{container.password}@{host}:{port}"
    conn = await asyncpg.connect(f"postgresql://{credentials}/{container.dbname}")
    try:
        await conn.execute(f"CREATE DATABASE {name}")
    finally:
        await conn.close()
    return load_settings(
        {
            "TELEGRAM_BOT_TOKEN": "123456:smoke-test",
            "TELEGRAM_NOTIFY_CHAT_ID": "-100125",
            "POSTGRES_DSN": f"postgresql://{credentials}/{name}",
            "YANDEX_FOLDER_ID": "folder",
            "YANDEX_API_KEY": "key",
        }
    )


def _stop_immediately(stop: asyncio.Event) -> None:
    stop.set()


async def test_bot_main_starts_and_reaches_polling(
    postgres_container: PostgresContainer, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = await _settings_on_fresh_db(postgres_container, "smoke_bot")
    polled: list[Bot] = []

    async def telegram_api(self: Bot, method: Any, request_timeout: int | None = None) -> Any:
        if isinstance(method, GetMe):
            return User(id=123456, is_bot=True, first_name="Bot", username="smoke_bot")
        return True

    async def start_polling(self: Dispatcher, bot: Bot, **kwargs: Any) -> None:
        polled.append(bot)

    async def stop_polling(self: Dispatcher) -> None:
        pass

    monkeypatch.setattr(Bot, "__call__", telegram_api)
    monkeypatch.setattr(Dispatcher, "start_polling", start_polling)
    monkeypatch.setattr(Dispatcher, "stop_polling", stop_polling)
    monkeypatch.setattr(bot_entrypoint, "register_shutdown", _stop_immediately)

    await bot_entrypoint.main(settings)

    assert len(polled) == 1


async def test_worker_main_starts_and_exits_on_stop(
    postgres_container: PostgresContainer, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = await _settings_on_fresh_db(postgres_container, "smoke_worker")
    monkeypatch.setattr(worker_entrypoint, "register_shutdown", _stop_immediately)

    await worker_entrypoint.main(settings)
