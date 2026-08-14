from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest
from aiogram.types import BufferedInputFile, InlineKeyboardMarkup

from content_zavod.domain import PlanId, TopicDraft
from content_zavod.telegram import (
    PlanItemId,
    PlanItemView,
    PlanMessageRef,
    PlanView,
    TelegramGateway,
    handle_topic_command,
)

_TZ = ZoneInfo("UTC")
_NOW = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)  # Monday of ISO week 2026-W33


class FakeBot:
    def __init__(self) -> None:
        self.sent_messages: list[tuple[int, str, InlineKeyboardMarkup | None]] = []
        self.edited_messages: list[tuple[int, int, str, InlineKeyboardMarkup | None]] = []

    async def send_message(self, chat_id, text, reply_markup=None) -> int:
        self.sent_messages.append((chat_id, text, reply_markup))
        return len(self.sent_messages)

    async def send_document(self, chat_id, document: BufferedInputFile, caption=None) -> None:
        raise AssertionError("not used by the /topic command")

    async def edit_message_text(self, chat_id, message_id, text, reply_markup=None) -> None:
        self.edited_messages.append((chat_id, message_id, text, reply_markup))


class FakePlan:
    def __init__(self, *, recent_titles: list[str] | None = None) -> None:
        self.recent_titles = list(recent_titles or [])
        self.added: list[tuple[str, list[TopicDraft]]] = []
        self.message_refs: dict[PlanId, PlanMessageRef] = {}
        self._plan = PlanView(
            id=PlanId("plan-1"),
            week_label="2026-W33",
            items=[
                PlanItemView(id=PlanItemId("item-1"), title="New Topic", status="pending_review")
            ],
        )

    async def recent_topic_titles(self, since: datetime) -> list[str]:
        return self.recent_titles

    async def add_topics(self, week_label: str, topics: list[TopicDraft]) -> PlanId:
        self.added.append((week_label, topics))
        return self._plan.id

    async def get(self, plan_id: PlanId) -> PlanView:
        assert plan_id == self._plan.id
        return self._plan

    async def get_message_ref(self, plan_id: PlanId) -> PlanMessageRef | None:
        return self.message_refs.get(plan_id)

    async def record_message_ref(self, plan_id: PlanId, chat_id: int, message_id: int) -> None:
        self.message_refs.setdefault(
            plan_id, PlanMessageRef(chat_id=chat_id, message_id=message_id)
        )


def _now() -> datetime:
    return _NOW


@pytest.mark.asyncio
async def test_handle_topic_command_adds_the_topic_to_the_current_weeks_plan() -> None:
    plan = FakePlan()
    gateway = TelegramGateway(FakeBot())

    await handle_topic_command(plan, gateway, chat_id=1, text="  New Topic  ", tz=_TZ, now=_now)

    assert plan.added == [("2026-W33", [TopicDraft(title="New Topic")])]


@pytest.mark.asyncio
async def test_handle_topic_command_sends_the_updated_plan_back_to_the_chat() -> None:
    plan = FakePlan()
    bot = FakeBot()
    gateway = TelegramGateway(bot)

    await handle_topic_command(plan, gateway, chat_id=1, text="New Topic", tz=_TZ, now=_now)

    assert len(bot.sent_messages) == 1
    chat_id, text, keyboard = bot.sent_messages[0]
    assert chat_id == 1
    assert "New Topic" in text
    assert keyboard is not None


@pytest.mark.asyncio
async def test_second_topic_for_the_same_plan_edits_the_canonical_message() -> None:
    """#73: a second /topic proposal for the same still-open week's Plan must edit the one
    canonical Plan message rather than posting a new one (ADR-0005)."""
    plan = FakePlan()
    bot = FakeBot()
    gateway = TelegramGateway(bot)

    await handle_topic_command(plan, gateway, chat_id=1, text="New Topic", tz=_TZ, now=_now)
    await handle_topic_command(plan, gateway, chat_id=1, text="Another Topic", tz=_TZ, now=_now)

    assert len(bot.sent_messages) == 1
    assert len(bot.edited_messages) == 1
    edited_chat_id, edited_message_id, _text, _keyboard = bot.edited_messages[0]
    assert (edited_chat_id, edited_message_id) == (1, 1)


@pytest.mark.asyncio
async def test_handle_topic_command_rejects_blank_text() -> None:
    plan = FakePlan()
    bot = FakeBot()
    gateway = TelegramGateway(bot)

    await handle_topic_command(plan, gateway, chat_id=1, text="   ", tz=_TZ, now=_now)

    assert plan.added == []
    assert len(bot.sent_messages) == 1
    assert "/topic" in bot.sent_messages[0][1]


@pytest.mark.asyncio
async def test_handle_topic_command_rejects_a_recently_used_title() -> None:
    plan = FakePlan(recent_titles=["Existing Topic"])
    bot = FakeBot()
    gateway = TelegramGateway(bot)

    await handle_topic_command(plan, gateway, chat_id=1, text="existing topic", tz=_TZ, now=_now)

    assert plan.added == []
    assert "уже использовалась" in bot.sent_messages[0][1]
