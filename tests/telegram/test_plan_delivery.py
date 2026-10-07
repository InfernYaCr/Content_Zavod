"""A deleted Plan message (#106): the next delivery sends a fresh one and makes it canonical,
instead of failing every later edit with «message to edit not found»."""

from __future__ import annotations

import asyncio

import pytest

from content_zavod.domain import PlanHubView
from content_zavod.telegram import (
    MessageGone,
    PlanId,
    PlanItemId,
    PlanItemView,
    PlanMessageRef,
    PlanView,
    TelegramGateway,
    deliver_plan_hub,
    deliver_plan_message,
)

PLAN_ID = PlanId("plan-1")
VIEW = PlanView(
    id=PLAN_ID,
    week_label="2026-W41",
    items=[PlanItemView(id=PlanItemId("item-1"), title="Тема", status="pending_review")],
)


class ChatBot:
    """A team chat: messages can be deleted by people, and editing one that's gone raises
    `MessageGone` the way the aiogram adapter does. Every call yields once, so concurrent
    deliveries interleave like real network calls."""

    def __init__(self) -> None:
        self.messages: dict[tuple[int, int], str] = {}
        self.sent: list[tuple[int, int]] = []
        self.edited: list[tuple[int, int]] = []
        self.deleted: list[tuple[int, int]] = []
        self._next_id = 100

    def delete_by_a_person(self, chat_id: int, message_id: int) -> None:
        del self.messages[(chat_id, message_id)]

    async def send_message(self, chat_id, text, reply_markup=None, parse_mode=None) -> int:
        await asyncio.sleep(0)
        message_id = self._next_id
        self._next_id += 1
        self.messages[(chat_id, message_id)] = text
        self.sent.append((chat_id, message_id))
        return message_id

    async def edit_message_text(self, chat_id, message_id, text, reply_markup=None) -> None:
        await asyncio.sleep(0)
        if (chat_id, message_id) not in self.messages:
            raise MessageGone(chat_id, message_id)
        self.messages[(chat_id, message_id)] = text
        self.edited.append((chat_id, message_id))

    async def delete_message(self, chat_id, message_id) -> None:
        await asyncio.sleep(0)
        self.messages.pop((chat_id, message_id), None)
        self.deleted.append((chat_id, message_id))


class FakePlan:
    """Same ref semantics as `domain.plan.Plan`: first record wins, replace only if unchanged."""

    def __init__(self, hub_status: str = "approved") -> None:
        self.ref: PlanMessageRef | None = None
        self.hub_status = hub_status

    async def get_message_ref(self, plan_id):
        await asyncio.sleep(0)
        return self.ref

    async def record_message_ref(self, plan_id, chat_id, message_id) -> None:
        await asyncio.sleep(0)
        if self.ref is None:
            self.ref = PlanMessageRef(chat_id=chat_id, message_id=message_id)

    async def replace_message_ref(self, plan_id, old, chat_id, message_id) -> bool:
        await asyncio.sleep(0)
        if self.ref != old:
            return False
        self.ref = PlanMessageRef(chat_id=chat_id, message_id=message_id)
        return True

    async def get_hub(self, plan_id) -> PlanHubView:
        return PlanHubView(id=plan_id, week_label="2026-W41", status=self.hub_status, topics=[])


async def test_deleted_plan_message_is_resent_and_the_ref_moves_to_it() -> None:
    bot, plan = ChatBot(), FakePlan()
    gateway = TelegramGateway(bot)
    await deliver_plan_message(plan, gateway, 42, VIEW)
    bot.delete_by_a_person(42, 100)

    sent_new = await deliver_plan_message(plan, gateway, 42, VIEW)

    assert sent_new is True
    assert bot.sent == [(42, 100), (42, 101)]
    assert plan.ref == PlanMessageRef(chat_id=42, message_id=101)

    sent_new = await deliver_plan_message(plan, gateway, 42, VIEW)  # the next one just edits

    assert sent_new is False
    assert bot.sent == [(42, 100), (42, 101)]
    assert bot.edited == [(42, 101)]


async def test_deleted_hub_message_is_resent_and_the_ref_moves_to_it() -> None:
    bot, plan = ChatBot(), FakePlan()
    gateway = TelegramGateway(bot)
    plan.ref = PlanMessageRef(chat_id=42, message_id=5)  # deleted before this delivery

    await deliver_plan_hub(plan, gateway, 42, PLAN_ID)
    await deliver_plan_hub(plan, gateway, 42, PLAN_ID)

    assert bot.sent == [(42, 100)]
    assert plan.ref == PlanMessageRef(chat_id=42, message_id=100)
    assert bot.edited == [(42, 100)]


async def test_racing_deliveries_after_a_deletion_leave_one_message() -> None:
    """Two notifications both hit the dead message: only one replacement message survives,
    the loser tidies up its own and redraws the winner's."""
    bot, plan = ChatBot(), FakePlan()
    gateway = TelegramGateway(bot)
    plan.ref = PlanMessageRef(chat_id=42, message_id=5)

    await asyncio.gather(
        deliver_plan_hub(plan, gateway, 42, PLAN_ID),
        deliver_plan_message(plan, gateway, 42, VIEW),
    )

    assert len(bot.sent) == 2
    assert len(bot.deleted) == 1
    assert list(bot.messages) == [(plan.ref.chat_id, plan.ref.message_id)]
    assert (plan.ref.chat_id, plan.ref.message_id) in bot.edited  # loser's view is drawn


async def test_a_delivery_that_loses_the_race_edits_the_winners_message() -> None:
    bot, plan = ChatBot(), FakePlan()
    gateway = TelegramGateway(bot)
    plan.ref = PlanMessageRef(chat_id=42, message_id=5)
    bot.messages[(42, 77)] = "winner"
    real_send = bot.send_message

    async def send_while_another_delivery_wins(*args, **kwargs) -> int:
        plan.ref = PlanMessageRef(chat_id=42, message_id=77)
        return await real_send(*args, **kwargs)

    bot.send_message = send_while_another_delivery_wins  # type: ignore[method-assign]

    await deliver_plan_message(plan, gateway, 42, VIEW)

    assert plan.ref == PlanMessageRef(chat_id=42, message_id=77)
    assert bot.deleted == [(42, 100)]
    assert bot.edited == [(42, 77)]


async def test_other_edit_failures_still_propagate() -> None:
    bot, plan = ChatBot(), FakePlan()
    plan.ref = PlanMessageRef(chat_id=42, message_id=5)
    bot.messages[(42, 5)] = "plan"

    async def flaky(*args, **kwargs) -> None:
        raise RuntimeError("network")

    bot.edit_message_text = flaky  # type: ignore[method-assign]

    with pytest.raises(RuntimeError):
        await deliver_plan_message(plan, TelegramGateway(bot), 42, VIEW)
    assert bot.sent == []
    assert plan.ref == PlanMessageRef(chat_id=42, message_id=5)
