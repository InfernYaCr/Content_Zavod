"""handle_generate_plan_command: the manual /generate_plan command (see scheduling/weekly_plan_trigger.py).

Available to both roles - it enqueues the same idempotent `Plan.request_new`
the weekly cron trigger calls, so a manual run and the schedule racing each
other collapse into one Job. If the week already has an active Plan, asks
for confirmation before archiving it and starting a fresh one, so a
mis-tap can't silently discard a Plan someone was mid-review on.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Protocol
from zoneinfo import ZoneInfo

from ..domain import PlanId
from ..scheduling import week_label_for
from .gateway import TelegramGateway, build_confirm_keyboard
from .types import PlanView


class PlanGeneration(Protocol):
    async def find_active(self, week_label: str) -> PlanView | None: ...

    async def get(self, plan_id: PlanId) -> PlanView: ...

    async def archive(self, plan_id: PlanId) -> None: ...

    async def request_new(self, week_label: str, *, generation_id: str | None = None) -> object: ...

    async def request_replacement(self, plan_id: PlanId) -> object: ...


async def handle_generate_plan_command(
    plan: PlanGeneration,
    gateway: TelegramGateway,
    chat_id: int,
    *,
    tz: ZoneInfo,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> None:
    week_label = week_label_for(now(), tz)
    active = await plan.find_active(week_label)
    if active is None:
        await plan.request_new(week_label)
        await gateway.send_notice(chat_id, f"Генерирую План на {week_label}...")
        return
    await gateway.send_message(
        chat_id,
        f"План на {week_label} уже существует. Перегенерировать полностью?",
        reply_markup=build_confirm_keyboard(active.id),
    )


async def handle_confirm_regenerate_plan(
    plan: PlanGeneration, gateway: TelegramGateway, chat_id: int, message_id: int, plan_id: PlanId
) -> None:
    await plan.request_replacement(plan_id)
    await gateway.edit_notice(chat_id, message_id, "Генерирую новый План...")


async def handle_cancel_regenerate_plan(
    gateway: TelegramGateway, chat_id: int, message_id: int
) -> None:
    await gateway.edit_notice(chat_id, message_id, "Отменено.")
