from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from content_zavod.domain import PlanId, PlanView
from content_zavod.telegram.generate_plan_command import (
    handle_cancel_regenerate_plan,
    handle_confirm_regenerate_plan,
    handle_generate_plan_command,
)

MOSCOW = ZoneInfo("Europe/Moscow")
FIXED_NOW = datetime(2026, 8, 3, 6, 0, tzinfo=timezone.utc)  # 2026-W32 Monday


class FakePlan:
    def __init__(self, active: PlanView | None = None) -> None:
        self._active = active
        self.requested: list[str] = []
        self.archived: list[PlanId] = []

    async def find_active(self, week_label: str) -> PlanView | None:
        return self._active

    async def get(self, plan_id: PlanId) -> PlanView:
        assert self._active is not None and self._active.id == plan_id
        return self._active

    async def archive(self, plan_id: PlanId) -> None:
        self.archived.append(plan_id)

    async def request_new(self, week_label: str) -> int:
        self.requested.append(week_label)
        return 1


class FakeGateway:
    def __init__(self) -> None:
        self.sent_notices: list[tuple[int, str]] = []
        self.sent_messages: list[tuple[int, str, object]] = []
        self.edited: list[tuple[int, int, str]] = []

    async def send_notice(self, chat_id, text) -> None:
        self.sent_notices.append((chat_id, text))

    async def send_message(self, chat_id, text, reply_markup=None) -> int:
        self.sent_messages.append((chat_id, text, reply_markup))
        return 1

    async def edit_notice(self, chat_id, message_id, text) -> None:
        self.edited.append((chat_id, message_id, text))


@pytest.mark.asyncio
async def test_no_active_plan_requests_new_directly() -> None:
    plan, gateway = FakePlan(active=None), FakeGateway()

    await handle_generate_plan_command(plan, gateway, chat_id=1, tz=MOSCOW, now=lambda: FIXED_NOW)

    assert plan.requested == ["2026-W32"]
    assert gateway.sent_notices == [(1, "Генерирую План на 2026-W32...")]
    assert gateway.sent_messages == []


@pytest.mark.asyncio
async def test_active_plan_prompts_confirmation_instead_of_regenerating() -> None:
    active = PlanView(id=PlanId("plan-1"), week_label="2026-W32", items=[])
    plan, gateway = FakePlan(active=active), FakeGateway()

    await handle_generate_plan_command(plan, gateway, chat_id=1, tz=MOSCOW, now=lambda: FIXED_NOW)

    assert plan.requested == []
    assert len(gateway.sent_messages) == 1
    chat_id, text, keyboard = gateway.sent_messages[0]
    assert "уже существует" in text
    assert keyboard is not None


@pytest.mark.asyncio
async def test_confirm_archives_old_plan_and_requests_a_new_one() -> None:
    active = PlanView(id=PlanId("plan-1"), week_label="2026-W32", items=[])
    plan, gateway = FakePlan(active=active), FakeGateway()

    await handle_confirm_regenerate_plan(plan, gateway, chat_id=1, message_id=5, plan_id=PlanId("plan-1"))

    assert plan.archived == [PlanId("plan-1")]
    assert plan.requested == ["2026-W32"]
    assert gateway.edited == [(1, 5, "Генерирую новый План...")]


@pytest.mark.asyncio
async def test_cancel_leaves_the_plan_untouched() -> None:
    plan, gateway = FakePlan(), FakeGateway()

    await handle_cancel_regenerate_plan(gateway, chat_id=1, message_id=5)

    assert plan.archived == []
    assert plan.requested == []
    assert gateway.edited == [(1, 5, "Отменено.")]
