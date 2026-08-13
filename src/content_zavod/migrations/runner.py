"""MigrationRunner: applies versioned SQL migrations instead of `CREATE TABLE IF NOT
EXISTS` schema-on-startup (#71).

Each `.sql` file under a migrations directory is one migration, named
`NNNN_description.sql` so filename order is application order. Applied
versions are recorded in `schema_migrations`; `run_pending` applies whatever
isn't recorded yet, each migration in its own transaction alongside the
tracking-row insert, so a failure partway through leaves it unrecorded and
the next run retries it rather than silently skipping it.

Migrations are forward-only and additive by convention (see
docs/adr/0013-versioned-migrations-replace-schema-on-startup.md for the
expand/contract rollout shape and rollback story): there is no `down.sql`
runner here, because every shipped migration is either idempotent
(`IF NOT EXISTS`) or, where it changes existing rows, safe to reverse by hand
per that ADR.

`bot` and `worker` each call `run_migrations` independently at their own
startup, so a deploy that restarts both at once has two runners racing
against the same database. `run_pending` holds a Postgres advisory lock for
its whole run: the second runner blocks until the first finishes and
releases it, then finds nothing pending instead of double-applying a
migration or crashing on a duplicate `schema_migrations` insert.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import asyncpg

_TRACKING_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version TEXT PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""

# Arbitrary fixed key for the advisory lock serializing concurrent
# `run_pending` calls (e.g. bot and worker starting together) - any int64
# works as long as it's not reused for an unrelated lock elsewhere.
_ADVISORY_LOCK_KEY = 727_100_071

SQL_DIR = Path(__file__).parent / "sql"


@dataclass(frozen=True)
class Migration:
    version: str
    sql: str


def load_migrations(directory: Path) -> list[Migration]:
    return [
        Migration(version=path.stem, sql=path.read_text(encoding="utf-8"))
        for path in sorted(directory.glob("*.sql"))
    ]


class MigrationRunner:
    def __init__(self, pool: asyncpg.Pool, migrations_dir: Path = SQL_DIR) -> None:
        self._pool = pool
        self._migrations = load_migrations(migrations_dir)

    async def run_pending(self) -> list[str]:
        """Apply migrations not yet recorded in `schema_migrations`, in filename order.

        Returns the versions actually applied by this call (empty when the
        schema is already current), so callers can log what changed. Holds a
        session-level advisory lock for the whole call so two callers racing
        at startup serialize instead of both trying to apply the same
        migration.
        """
        async with self._pool.acquire() as conn:
            await conn.execute("SELECT pg_advisory_lock($1)", _ADVISORY_LOCK_KEY)
            try:
                return await self._apply_pending(conn)
            finally:
                await conn.execute("SELECT pg_advisory_unlock($1)", _ADVISORY_LOCK_KEY)

    async def _apply_pending(self, conn: asyncpg.Connection) -> list[str]:
        await conn.execute(_TRACKING_TABLE_SQL)
        applied_rows = await conn.fetch("SELECT version FROM schema_migrations")
        already_applied = {row["version"] for row in applied_rows}

        newly_applied: list[str] = []
        for migration in self._migrations:
            if migration.version in already_applied:
                continue
            async with conn.transaction():
                await conn.execute(migration.sql)
                await conn.execute(
                    "INSERT INTO schema_migrations (version) VALUES ($1)", migration.version
                )
            newly_applied.append(migration.version)
        return newly_applied


async def run_migrations(pool: asyncpg.Pool, migrations_dir: Path = SQL_DIR) -> list[str]:
    return await MigrationRunner(pool, migrations_dir).run_pending()
