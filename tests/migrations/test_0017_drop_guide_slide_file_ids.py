"""0017 (#152): the old carousel's cached picture file_ids go; the kept pictures' stay."""

from __future__ import annotations

import shutil
from pathlib import Path

import asyncpg

from content_zavod.migrations import SQL_DIR, run_migrations


async def test_0017_drops_only_the_removed_pictures_file_ids(
    isolated_pool: asyncpg.Pool, tmp_path: Path
) -> None:
    for path in SQL_DIR.glob("*.sql"):
        if path.stem[:4] <= "0016":
            shutil.copy(path, tmp_path / path.name)
    await run_migrations(isolated_pool, tmp_path)
    await isolated_pool.execute(
        """
        INSERT INTO bot_state (key, value) VALUES
            ('asset_file_id:about', 'a:1'), ('asset_file_id:faq', 'a:2'),
            ('asset_file_id:welcome', 'a:3'), ('asset_file_id:week', 'a:4'),
            ('telegraph_access_token', 't');
        """
    )

    shutil.copy(SQL_DIR / "0017_drop_guide_slide_file_ids.sql", tmp_path)
    assert await run_migrations(isolated_pool, tmp_path) == ["0017_drop_guide_slide_file_ids"]

    keys = await isolated_pool.fetch("SELECT key FROM bot_state ORDER BY key")
    assert [row["key"] for row in keys] == [
        "asset_file_id:week",
        "asset_file_id:welcome",
        "telegraph_access_token",
    ]
