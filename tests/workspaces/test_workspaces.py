from __future__ import annotations

import asyncio

import asyncpg

from content_zavod.workspaces import Workspaces


async def test_get_or_create_makes_one_workspace_per_owner(pool: asyncpg.Pool) -> None:
    workspaces = Workspaces(pool)

    first, second = await asyncio.gather(
        workspaces.get_or_create(5001, timezone="Europe/Moscow", owner_username="anna"),
        workspaces.get_or_create(5001, timezone="Asia/Novosibirsk"),
    )

    assert first.id == second.id
    assert await workspaces.get_by_owner(5001) == await workspaces.get(first.id)
    stored = await workspaces.get(first.id)
    assert stored is not None
    assert stored.owner_telegram_id == 5001
    assert stored.owner_username == "anna"
    assert stored.plan_limit is None and stored.article_limit is None


async def test_a_known_owner_keeps_timezone_and_gets_a_fresh_username(pool: asyncpg.Pool) -> None:
    workspaces = Workspaces(pool)
    created = await workspaces.get_or_create(5002, timezone="Europe/Moscow", owner_username="old")

    again = await workspaces.get_or_create(5002, timezone="Asia/Tokyo", owner_username="new")

    assert again.id == created.id
    assert again.timezone == "Europe/Moscow"
    assert again.owner_username == "new"
    assert (await workspaces.get_or_create(5002, timezone="UTC")).owner_username == "new"


async def test_unknown_workspace_or_owner_is_none(pool: asyncpg.Pool) -> None:
    workspaces = Workspaces(pool)

    assert await workspaces.get_by_owner(-1) is None
    assert await workspaces.get(10**12) is None


async def test_a_403_marks_the_workspace_blocked_until_the_next_start(pool: asyncpg.Pool) -> None:
    workspaces = Workspaces(pool)
    created = await workspaces.get_or_create(5003, timezone="Europe/Moscow")

    await workspaces.mark_blocked(created.id)
    blocked = await pool.fetchval("SELECT bot_blocked_at FROM workspaces WHERE id = $1", created.id)
    await workspaces.get_or_create(5003, timezone="Europe/Moscow")
    after_start = await pool.fetchval(
        "SELECT bot_blocked_at FROM workspaces WHERE id = $1", created.id
    )

    assert blocked is not None
    assert after_start is None
