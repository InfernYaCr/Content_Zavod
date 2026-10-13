"""Лимит (#118, ADR-0015 «Лимит», «Защита от лавины»): what a Пространство may start.

One check, `Limits.reserve`, runs before every generation in the same transaction as its
`enqueue`, under a lock on the Workspace's row, in order:

1. no unfinished Job (queued, running, or finished with its notification not yet handled) -
   «Дождитесь окончания текущей генерации»;
2. fewer than `jobs_per_day` Jobs created in the last 24 hours;
3. what the Job type costs: a Plan (charged + requested ≤ Лимит of Планов, and at most
   `plan_jobs_per_day` runs a day), a Статья (≤ Лимит of Статей), or nothing of the Лимит -
   a cover, a Тема regeneration, a style analysis - braked by `free_jobs_per_day` instead.

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
    `FREE_JOBS_PER_DAY_LIMIT`, `LIMIT_CONTACT`): the Лимит of every Workspace whose own
    `plan_limit`/`article_limit` is `NULL`, so a change in env applies to all of them at once.
    `plan_jobs_per_day` caps `generate_plan` runs (each may rebuild the Niche Map: a model call
    and Wordstat requests from a quota shared by all Workspaces), reruns and replacements
    included."""

    plans: int = 1
    articles: int = 8
    jobs_per_day: int = 30
    free_jobs_per_day: int = 10
    plan_jobs_per_day: int = 5
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
            custom=plan_limit is not None or article_limit is not None,
        )


@dataclass(frozen=True)
class Usage:
    """`custom` - a Лимит an administrator set for this Workspace (`/admin`), not the trial."""

    plans_used: int
    plan_limit: int
    articles_used: int
    article_limit: int
    custom: bool = False

    @property
    def plans_left(self) -> int:
        return max(0, self.plan_limit - self.plans_used)

    @property
    def articles_left(self) -> int:
        return max(0, self.article_limit - self.articles_used)


# What a Job type costs: a Plan, a Статья, or nothing of the Лимит («free» - a cover, a Тема
# regeneration, a style analysis; braked by their own daily cap instead).
PLAN_JOB = "generate_plan"
ARTICLE_JOBS = ("generate_article", "regenerate_article")


class LimitRefused(Exception):
    """A generation this Пространство may not start now. Nothing was enqueued."""


class GenerationBusy(LimitRefused):
    def __init__(self) -> None:
        super().__init__("the Workspace has an unfinished Job")


class DailyJobLimitReached(LimitRefused):
    def __init__(self) -> None:
        super().__init__(f"the Workspace reached a daily Job cap ({type(self).__name__})")


class DailyPlanLimitReached(DailyJobLimitReached):
    """`plan_jobs_per_day` `generate_plan` runs in the last 24 hours."""


class DailyFreeJobLimitReached(DailyJobLimitReached):
    """`free_jobs_per_day` Jobs that cost no Лимит in the last 24 hours."""


class TrialLimitExhausted(LimitRefused):
    """`plans`/`articles` - what the refused action asked for."""

    def __init__(self, usage: Usage, *, plans: int, articles: int, contact: str | None) -> None:
        super().__init__(f"Лимит exceeded: asked plans={plans} articles={articles}, {usage}")
        self.usage = usage
        self.plans = plans
        self.articles = articles
        self.contact = contact


class _Quota:
    """What is left today and of the Лимит, and what this one check has asked for so far."""

    def __init__(self, usage: Usage, counts: asyncpg.Record, trial: TrialLimits) -> None:
        self.usage = usage
        self._counts = counts
        self._trial = trial
        self._asked = {"plan": 0, "article": 0, "free": 0}

    def check(self, job_type: str | None, count: int) -> None:
        """In order: no unfinished Job, the total daily cap, then what `job_type` costs."""
        if self._counts["busy"]:
            raise GenerationBusy()
        if self._counts["jobs_today"] >= self._trial.jobs_per_day:
            raise DailyJobLimitReached()
        if job_type is not None:
            self.require(job_type, count)

    def require(self, job_type: str, count: int) -> None:
        if job_type == PLAN_JOB:
            asked = self._asked["plan"] + count
            if asked > self.usage.plans_left:
                raise TrialLimitExhausted(
                    self.usage, plans=asked, articles=0, contact=self._trial.contact
                )
            if self._counts["plan_jobs_today"] + asked > self._trial.plan_jobs_per_day:
                raise DailyPlanLimitReached()
            self._asked["plan"] = asked
        elif job_type in ARTICLE_JOBS:
            asked = self._asked["article"] + count
            if asked > self.usage.articles_left:
                raise TrialLimitExhausted(
                    self.usage, plans=0, articles=asked, contact=self._trial.contact
                )
            self._asked["article"] = asked
        else:
            asked = self._asked["free"] + count
            if self._counts["free_jobs_today"] + asked > self._trial.free_jobs_per_day:
                raise DailyFreeJobLimitReached()
            self._asked["free"] = asked


class Reservation:
    """The checked transaction: enqueue on `conn`. `require` adds a request whose size is only
    known once the lock is held (the Статьи and covers of «Утвердить всё»)."""

    def __init__(self, conn: asyncpg.Connection, quota: _Quota) -> None:
        self.conn = conn
        self._quota = quota

    @property
    def usage(self) -> Usage:
        return self._quota.usage

    def require(self, job_type: str, count: int = 1) -> None:
        self._quota.require(job_type, count)


_LOCK = "SELECT plan_limit, article_limit FROM workspaces WHERE id = $1 FOR NO KEY UPDATE"
_READ = "SELECT plan_limit, article_limit FROM workspaces WHERE id = $1"
# One statement, so one snapshot: a Job seen as notified has its charge seen too (the charge
# commits before `mark_notified`). Run after the lock is taken, never in the locking statement:
# that one's snapshot predates the wait for the lock. A «🔁 Повторить» reuses its Job, so a
# retry is checked against these caps but adds nothing to them (ADR-0015).
_COUNTS = """
SELECT
    EXISTS (SELECT 1 FROM jobs WHERE workspace_id = $1 AND notified_at IS NULL) AS busy,
    count(*) AS jobs_today,
    count(*) FILTER (WHERE job_type = $2) AS plan_jobs_today,
    count(*) FILTER (WHERE job_type <> $2 AND job_type <> ALL($3::text[])) AS free_jobs_today,
    (SELECT count(*) FROM limit_charges WHERE workspace_id = $1 AND unit = 'plan') AS plans,
    (SELECT count(*) FROM limit_charges WHERE workspace_id = $1 AND unit = 'article') AS articles
FROM jobs
WHERE workspace_id = $1 AND created_at > now() - interval '24 hours'
"""


class Limits:
    def __init__(self, pool: asyncpg.Pool, trial: TrialLimits) -> None:
        self._pool = pool
        self._trial = trial

    @property
    def contact(self) -> str | None:
        """`LIMIT_CONTACT`: whom to ask for more."""
        return self._trial.contact

    async def usage(self, workspace_id: WorkspaceId) -> Usage:
        """Charged and Лимит, for the Главное меню and `/admin`."""
        return (await self._quota(self._pool, _READ, workspace_id)).usage

    async def precheck(self, workspace_id: WorkspaceId, job_type: str) -> None:
        """The same refusals without the lock - to refuse at the press, before asking for a
        comment. Only `reserve` decides."""
        (await self._quota(self._pool, _READ, workspace_id)).check(job_type, 1)

    @asynccontextmanager
    async def reserve(
        self, workspace_id: WorkspaceId, job_type: str | None = None, count: int = 1
    ) -> AsyncIterator[Reservation]:
        """`count` Jobs of `job_type` (none yet: add them with `Reservation.require`). Raises
        `LimitRefused` before yielding; leaving the block commits. E.g. a style analysis:
        `async with limits.reserve(workspace.id, "analyze_style") as reservation:` and
        enqueue with `conn=reservation.conn`."""
        async with self._pool.acquire() as conn, conn.transaction():
            quota = await self._quota(conn, _LOCK, workspace_id)
            quota.check(job_type, count)
            yield Reservation(conn, quota)

    async def _quota(
        self, db: asyncpg.Pool | asyncpg.Connection, limits_sql: str, workspace_id: WorkspaceId
    ) -> _Quota:
        limits = await db.fetchrow(limits_sql, workspace_id)
        counts = await db.fetchrow(_COUNTS, workspace_id, PLAN_JOB, list(ARTICLE_JOBS))
        assert limits is not None and counts is not None
        usage = self._trial.usage(
            plan_limit=limits["plan_limit"],
            article_limit=limits["article_limit"],
            plans_used=counts["plans"],
            articles_used=counts["articles"],
        )
        return _Quota(usage, counts, self._trial)


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
