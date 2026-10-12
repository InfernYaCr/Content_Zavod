"""0013 (ADR-0015): Roles and the team chat's leftovers go; the interim team Workspace is
blocked so the Расписание tick leaves it alone, while people's own Workspaces are untouched."""

from __future__ import annotations

import shutil
from pathlib import Path

import asyncpg

from content_zavod.migrations import SQL_DIR, run_migrations


async def test_0013_drops_roles_and_the_team_note_and_blocks_the_team_workspace(
    isolated_pool: asyncpg.Pool, tmp_path: Path
) -> None:
    for path in SQL_DIR.glob("*.sql"):
        if path.stem[:4] <= "0012":
            shutil.copy(path, tmp_path / path.name)
    await run_migrations(isolated_pool, tmp_path)
    await isolated_pool.execute(
        """
        INSERT INTO members (telegram_id, role) VALUES (1, 'owner');
        INSERT INTO join_requests (telegram_id) VALUES (2);
        INSERT INTO join_request_broadcasts (join_request_id, owner_telegram_id, chat_id, message_id)
            SELECT id, 1, 1, 5 FROM join_requests;
        INSERT INTO bot_state (key, value) VALUES
            ('team_note', '-100:7'), ('telegraph_access_token', 't');
        INSERT INTO workspaces (owner_telegram_id, timezone) VALUES
            (-100500, 'Europe/Moscow'), (7001, 'Europe/Moscow');
        """
    )

    assert await run_migrations(isolated_pool) == ["0013_drop_roles"]

    for table in ("members", "join_requests", "join_request_broadcasts"):
        assert await isolated_pool.fetchval("SELECT to_regclass($1)", table) is None, table
    keys = await isolated_pool.fetch("SELECT key FROM bot_state ORDER BY key")
    assert [row["key"] for row in keys] == ["telegraph_access_token"]
    blocked = await isolated_pool.fetch(
        "SELECT owner_telegram_id, bot_blocked_at IS NOT NULL AS blocked FROM workspaces "
        "ORDER BY owner_telegram_id"
    )
    assert [(row["owner_telegram_id"], row["blocked"]) for row in blocked] == [
        (-100500, True),
        (7001, False),
    ]
