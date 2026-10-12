from __future__ import annotations

import asyncio

import asyncpg

from content_zavod.entrypoints._team_workspace import TeamScoped
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


async def test_team_scoped_passes_the_workspace_first_and_keeps_plain_attributes() -> None:
    class Repository:
        limit = 3

        async def get(self, workspace_id: int, item_id: str) -> tuple[int, str]:
            return workspace_id, item_id

    scoped = TeamScoped(Repository(), 7)

    assert await scoped.get("x") == (7, "x")
    assert scoped.limit == 3
