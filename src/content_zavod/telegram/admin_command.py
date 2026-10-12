"""`/admin` of the Глобальный администратор (ADR-0015): the Пространства with their Лимит
«charged/Лимит» and the spend over 30 days; `/admin limit <id> <планы> <статьи>` sets one's
Лимит. Only metadata and money - a Тема, a Статья or a Настройка never reaches this screen.
Who may run it is the bot's filter (`is_global_admin`); everyone else's `/admin` is an unknown
command.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from typing import Protocol

from ..domain import UnitUsage
from ..workspaces import WorkspaceId
from ..workspaces.admin import WorkspaceOverview
from .gateway import TelegramGateway
from .texts import (
    ADMIN_COST,
    ADMIN_COST_INCOMPLETE,
    ADMIN_CUSTOM_LIMIT,
    ADMIN_EMPTY,
    ADMIN_HINT,
    ADMIN_LIMIT_NOT_FOUND,
    ADMIN_LIMIT_SET,
    ADMIN_NO_ACTIVITY,
    ADMIN_NO_SPEND,
    ADMIN_NO_USERNAME,
    ADMIN_TITLE,
    ADMIN_UNITS,
    ADMIN_WORKSPACE,
)

# One message: ~170 characters each keeps the list under Telegram's 4096.
SHOWN_WORKSPACES = 20
SPEND_PERIOD = timedelta(days=30)


class AdminRegistry(Protocol):
    async def overview(self, *, limit: int) -> tuple[list[WorkspaceOverview], int]: ...

    async def set_limits(
        self, admin_telegram_id: int, workspace_id: WorkspaceId, plans: int, articles: int
    ) -> bool: ...


class SpendSource(Protocol):
    async def usage_for(self, workspace_id: WorkspaceId, since: datetime) -> list[UnitUsage]: ...


async def handle_admin_command(
    admin: AdminRegistry,
    spend: SpendSource,
    gateway: TelegramGateway,
    chat_id: int,
    admin_telegram_id: int,
    args: str,
    *,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> None:
    parts = args.split()
    if not parts:
        await gateway.send_notice(chat_id, await _overview_text(admin, spend, now()))
        return
    numbers = parts[1:]
    if parts[0] != "limit" or len(numbers) != 3 or not all(n.isdecimal() for n in numbers):
        await gateway.send_notice(chat_id, ADMIN_HINT)
        return
    workspace_id, plans, articles = (int(n) for n in numbers)
    if not await admin.set_limits(admin_telegram_id, WorkspaceId(workspace_id), plans, articles):
        await gateway.send_notice(chat_id, ADMIN_LIMIT_NOT_FOUND.format(id=workspace_id))
        return
    await gateway.send_notice(
        chat_id, ADMIN_LIMIT_SET.format(id=workspace_id, plans=plans, articles=articles)
    )


async def _overview_text(admin: AdminRegistry, spend: SpendSource, now: datetime) -> str:
    workspaces, total = await admin.overview(limit=SHOWN_WORKSPACES)
    if not workspaces:
        return ADMIN_EMPTY
    entries = [
        _workspace_text(workspace, await spend.usage_for(workspace.id, now - SPEND_PERIOD))
        for workspace in workspaces
    ]
    title = ADMIN_TITLE.format(shown=len(workspaces), total=total)
    return "\n\n".join([title, *entries, ADMIN_HINT])


def _workspace_text(workspace: WorkspaceOverview, spend: Sequence[UnitUsage]) -> str:
    usage = workspace.usage
    return ADMIN_WORKSPACE.format(
        id=workspace.id,
        username=f"@{workspace.owner_username}" if workspace.owner_username else ADMIN_NO_USERNAME,
        created=_date(workspace.created_at),
        active=_date(workspace.last_activity) if workspace.last_activity else ADMIN_NO_ACTIVITY,
        plans_used=usage.plans_used,
        plan_limit=usage.plan_limit,
        articles_used=usage.articles_used,
        article_limit=usage.article_limit,
        custom=ADMIN_CUSTOM_LIMIT if workspace.custom_limit else "",
        spend=_spend_text(spend),
    )


def _spend_text(spend: Sequence[UnitUsage]) -> str:
    """«12345 ток., 3 поиск. · ≈ 12.34 ₽», marked incomplete when a step had no price."""
    if not spend:
        return ADMIN_NO_SPEND
    quantities = ", ".join(f"{u.quantity} {ADMIN_UNITS.get(u.unit, u.unit)}" for u in spend)
    cost = ADMIN_COST.format(cost=sum(u.cost for u in spend))
    if not all(u.complete for u in spend):
        cost += ADMIN_COST_INCOMPLETE
    return f"{quantities} · {cost}"


def _date(moment: datetime) -> str:
    return moment.strftime("%d.%m.%Y")
