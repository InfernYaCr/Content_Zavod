"""Add (or promote) a Владелец in the allowlist: `python scripts/add_owner.py <telegram_id>`.

Applies pending migrations first, so it works on a fresh database before the bot has started.
"""

import asyncio
import sys

import asyncpg

from content_zavod.access import Membership
from content_zavod.config import load_settings
from content_zavod.migrations import run_migrations


async def main(telegram_id: int) -> None:
    pool = await asyncpg.create_pool(dsn=load_settings().postgres_dsn)
    try:
        await run_migrations(pool)
        await Membership(pool).add_member(telegram_id, "owner")
    finally:
        await pool.close()
    print(f"Владелец {telegram_id} добавлен")


if __name__ == "__main__":
    if len(sys.argv) != 2 or not sys.argv[1].isdigit():
        sys.exit("usage: python scripts/add_owner.py <telegram_id>")
    asyncio.run(main(int(sys.argv[1])))
