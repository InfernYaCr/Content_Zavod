"""The «командное» Пространство: the interim shim of ADR-0015, «Промежуточное состояние».

Until the Telegram layer resolves a Workspace from every update (PR 4 of #118), the bot runs
as today, on one Workspace owned by `TELEGRAM_NOTIFY_CHAT_ID`: a group id is negative, so it
can never collide with a person's own Workspace, and delivering to `owner_telegram_id` is
delivering to the team chat. `TeamScoped` hands that Workspace's id to every call of a
repository whose methods take `workspace_id` first, so the Telegram layer keeps its current
call shape. The worker no longer uses it: each Job Handler gets its claimed Job's own
`workspace_id`. The Telegram PR deletes this module.
"""

from __future__ import annotations

from functools import partial
from typing import Any

import asyncpg

from ..config import Settings
from ..workspaces import WorkspaceId, Workspaces


async def team_workspace_id(pool: asyncpg.Pool, settings: Settings) -> WorkspaceId:
    workspace = await Workspaces(pool).get_or_create(
        settings.telegram_notify_chat_id, timezone=settings.timezone.key
    )
    return workspace.id


class TeamScoped:
    """`TeamScoped(plan, ws).get(plan_id)` is `plan.get(ws, plan_id)` - every method of the
    wrapped object gets `workspace_id` as its first argument."""

    def __init__(self, target: Any, workspace_id: WorkspaceId) -> None:
        self._target = target
        self._workspace_id = workspace_id

    def __getattr__(self, name: str) -> Any:
        attribute = getattr(self._target, name)
        return partial(attribute, self._workspace_id) if callable(attribute) else attribute
