"""The Глобальный администратор's view (ADR-0015, «Команда Глобального администратора»).

The one module, besides the queue's worker side and the Расписание tick, that reads across all
Пространства (an exemption in `tests/test_tenant_sql.py`). It reads metadata only - owner,
dates, the Лимит and what was charged against it - never a Тема, a Статья or a Настройка; the
spend comes from `GenerationSteps.usage_for`, one Workspace at a time.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

import asyncpg

from .limits import TrialLimits, Usage
from .workspaces import WorkspaceId

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class WorkspaceOverview:
    id: WorkspaceId
    owner_username: str | None
    created_at: datetime
    # When its latest Job was created; `None` - it never started one.
    last_activity: datetime | None
    usage: Usage
    # The Глобальный администратор set its own numbers (`/admin limit`), not the config's.
    custom_limit: bool


class WorkspaceAdmin:
    def __init__(self, pool: asyncpg.Pool, trial: TrialLimits) -> None:
        self._pool = pool
        self._trial = trial

    async def overview(self, *, limit: int) -> tuple[list[WorkspaceOverview], int]:
        """The `limit` most recently active Workspaces, and how many there are in all."""
        total = await self._pool.fetchval("SELECT count(*) FROM workspaces")
        rows = await self._pool.fetch(
            """
            SELECT w.id, w.owner_username, w.created_at, w.plan_limit, w.article_limit,
                (SELECT max(j.created_at) FROM jobs j WHERE j.workspace_id = w.id)
                    AS last_activity,
                (SELECT count(*) FROM limit_charges c
                 WHERE c.workspace_id = w.id AND c.unit = 'plan') AS plans,
                (SELECT count(*) FROM limit_charges c
                 WHERE c.workspace_id = w.id AND c.unit = 'article') AS articles
            FROM workspaces w
            ORDER BY last_activity DESC NULLS LAST, w.created_at DESC
            LIMIT $1
            """,
            limit,
        )
        return [
            WorkspaceOverview(
                id=WorkspaceId(row["id"]),
                owner_username=row["owner_username"],
                created_at=row["created_at"],
                last_activity=row["last_activity"],
                usage=self._trial.usage(
                    plan_limit=row["plan_limit"],
                    article_limit=row["article_limit"],
                    plans_used=row["plans"],
                    articles_used=row["articles"],
                ),
                custom_limit=row["plan_limit"] is not None or row["article_limit"] is not None,
            )
            for row in rows
        ], total

    async def set_limits(
        self,
        admin_telegram_id: int,
        workspace_id: WorkspaceId,
        plans: int | None,
        articles: int | None,
    ) -> bool:
        """`/admin limit`: absolute numbers for one Workspace (`0 0` stops it; `None` - back to
        the config's). `False` - no such Workspace. Logged with who did it (a real audit log is
        C5)."""
        status = await self._pool.execute(
            "UPDATE workspaces SET plan_limit = $2, article_limit = $3, updated_at = now() "
            "WHERE id = $1",
            workspace_id,
            plans,
            articles,
        )
        if status != "UPDATE 1":
            return False
        logger.info(
            "Global admin %s set the Лимит of workspace %s: plans=%s articles=%s",
            admin_telegram_id,
            workspace_id,
            plans,
            articles,
        )
        return True
