"""Лимит (#118, ADR-0015 «Лимит», «Защита от лавины»): what a Пространство may start.

One check, `Limits.reserve`, runs before every generation in the same transaction as its
`enqueue`, under a lock on the Workspace's row, in order:

1. no unfinished Job (queued, running, or finished with its notification not yet handled) -
   «Дождитесь окончания текущей генерации»;
2. fewer than `jobs_per_day` Jobs created in the last 24 hours;
3. charged + requested ≤ Лимит, for Планы and Статьи separately (only for a non-zero request:
   a cover or a Тема regeneration costs no Лимит).

There is no separate "in flight" count: (1) guarantees that whatever should have been charged
is charged by the time of the check. The lock (`FOR NO KEY UPDATE`, which still lets the
`KEY SHARE` of a foreign key insert through) makes two simultaneous presses take turns - the
second sees the first one's Jobs and is refused. Lock order everywhere: the Workspace row
first, then Plan/Статья rows, then new `jobs` rows; a Job row already in the table is never
locked after it (`JobQueue.enqueue` is `ON CONFLICT DO NOTHING`), so this can't deadlock with
`claim_next`, which locks a queued Job and then the Workspace row.

Charges are written by `charge` when a result is applied (`Plan.add_topics`,
`Article.record_version`), in that same transaction: `limit_charges` has `job_id` as its key,
so a redelivered notification or a «🔁 Повторить» that finally succeeds charges once, and a
failed Job charges nothing.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Literal

import asyncpg

from .workspaces import WorkspaceId

Unit = Literal["plan", "article"]


@dataclass(frozen=True)
class TrialLimits:
    """The config side (`TRIAL_PLAN_LIMIT`, `TRIAL_ARTICLE_LIMIT`, `JOBS_PER_DAY_LIMIT`,
    `LIMIT_CONTACT`): the Лимит of every Workspace whose own `plan_limit`/`article_limit` is
    `NULL`, so a change in env applies to all of them at once."""

    plans: int = 1
    articles: int = 8
    jobs_per_day: int = 50
    contact: str | None = None

    def usage(
        self,
        *,
        plan_limit: int | None,
        article_limit: int | None,
        plans_used: int,
        articles_used: int,
    ) -> Usage:
        """A Workspace's `plan_limit`/`article_limit`, `NULL` = these."""
        return Usage(
            plans_used=plans_used,
            plan_limit=self.plans if plan_limit is None else plan_limit,
            articles_used=articles_used,
            article_limit=self.articles if article_limit is None else article_limit,
        )


@dataclass(frozen=True)
class Usage:
    plans_used: int
    plan_limit: int
    articles_used: int
    article_limit: int

    @property
    def plans_left(self) -> int:
        return max(0, self.plan_limit - self.plans_used)

    @property
    def articles_left(self) -> int:
        return max(0, self.article_limit - self.articles_used)


class LimitRefused(Exception):
    """A generation this Пространство may not start now. Nothing was enqueued."""


class GenerationBusy(LimitRefused):
    def __init__(self) -> None:
        super().__init__("the Workspace has an unfinished Job")


class DailyJobLimitReached(LimitRefused):
    def __init__(self) -> None:
        super().__init__("the Workspace reached its daily Job ceiling")


class TrialLimitExhausted(LimitRefused):
    """`plans`/`articles` - what the refused action asked for."""

    def __init__(self, usage: Usage, *, plans: int, articles: int, contact: str | None) -> None:
        super().__init__(f"Лимит exceeded: asked plans={plans} articles={articles}, {usage}")
        self.usage = usage
        self.plans = plans
        self.articles = articles
        self.contact = contact


@dataclass(frozen=True)
class Reservation:
    """The checked transaction: enqueue on `conn`. `require` checks a request whose size is only
    known once the lock is held (the Статьи of «Утвердить всё»)."""

    conn: asyncpg.Connection
    usage: Usage
    contact: str | None

    def require(self, *, plans: int = 0, articles: int = 0) -> None:
        _require(self.usage, plans, articles, self.contact)


_LOCK = "SELECT plan_limit, article_limit FROM workspaces WHERE id = $1 FOR NO KEY UPDATE"
_READ = "SELECT plan_limit, article_limit FROM workspaces WHERE id = $1"
# One statement, so one snapshot: a Job seen as notified has its charge seen too (the charge
# commits before `mark_notified`). Run after the lock is taken, never in the locking statement:
# that one's snapshot predates the wait for the lock.
_COUNTS = """
SELECT
    EXISTS (SELECT 1 FROM jobs WHERE workspace_id = $1 AND notified_at IS NULL) AS busy,
    (SELECT count(*) FROM jobs
     WHERE workspace_id = $1 AND created_at > now() - interval '24 hours') AS jobs_today,
    (SELECT count(*) FROM limit_charges WHERE workspace_id = $1 AND unit = 'plan') AS plans,
    (SELECT count(*) FROM limit_charges WHERE workspace_id = $1 AND unit = 'article') AS articles
"""


class Limits:
    def __init__(self, pool: asyncpg.Pool, trial: TrialLimits) -> None:
        self._pool = pool
        self._trial = trial

    async def usage(self, workspace_id: WorkspaceId) -> Usage:
        """Charged and Лимит, for the menu's «Осталось» line and `/admin`."""
        limits = await self._pool.fetchrow(_READ, workspace_id)
        counts = await self._pool.fetchrow(_COUNTS, workspace_id)
        assert limits is not None and counts is not None
        return self._usage(limits, counts)

    async def precheck(
        self, workspace_id: WorkspaceId, *, plans: int = 0, articles: int = 0
    ) -> None:
        """The same refusals without the lock - to refuse at the press, before asking for a
        comment. Only `reserve` decides."""
        await self._check(self._pool, _READ, workspace_id, plans, articles)

    @asynccontextmanager
    async def reserve(
        self, workspace_id: WorkspaceId, *, plans: int = 0, articles: int = 0
    ) -> AsyncIterator[Reservation]:
        """Raises `LimitRefused` before yielding; leaving the block commits."""
        async with self._pool.acquire() as conn, conn.transaction():
            usage = await self._check(conn, _LOCK, workspace_id, plans, articles)
            yield Reservation(conn, usage, self._trial.contact)

    async def _check(
        self,
        db: asyncpg.Pool | asyncpg.Connection,
        limits_sql: str,
        workspace_id: WorkspaceId,
        plans: int,
        articles: int,
    ) -> Usage:
        limits = await db.fetchrow(limits_sql, workspace_id)
        counts = await db.fetchrow(_COUNTS, workspace_id)
        assert limits is not None and counts is not None
        if counts["busy"]:
            raise GenerationBusy()
        if counts["jobs_today"] >= self._trial.jobs_per_day:
            raise DailyJobLimitReached()
        usage = self._usage(limits, counts)
        _require(usage, plans, articles, self._trial.contact)
        return usage

    def _usage(self, limits: asyncpg.Record, counts: asyncpg.Record) -> Usage:
        return self._trial.usage(
            plan_limit=limits["plan_limit"],
            article_limit=limits["article_limit"],
            plans_used=counts["plans"],
            articles_used=counts["articles"],
        )


def _require(usage: Usage, plans: int, articles: int, contact: str | None) -> None:
    if (plans and plans > usage.plans_left) or (articles and articles > usage.articles_left):
        raise TrialLimitExhausted(usage, plans=plans, articles=articles, contact=contact)


async def charge(
    conn: asyncpg.Connection, workspace_id: WorkspaceId, job_id: int, unit: Unit
) -> None:
    """Charge one applied result - in the transaction that applied it. Once per Job."""
    await conn.execute(
        "INSERT INTO limit_charges (job_id, workspace_id, unit) VALUES ($1, $2, $3) "
        "ON CONFLICT (job_id) DO NOTHING",
        job_id,
        workspace_id,
        unit,
    )
