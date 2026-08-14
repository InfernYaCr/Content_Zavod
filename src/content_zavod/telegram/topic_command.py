"""handle_topic_command: manual /topic proposal - the second of ADR-0006's two equal Тема inputs.

Needs no LLM or Job Queue (see #10): turning free text into a TopicDraft is a
pure, synchronous operation, unlike generate_plan's Wordstat+LLM drafting.
Reuses `Plan.add_topics` (see domain/plan.py) so a manual proposal and an
automatic generate_plan run for the same week combine into one draft Plan
instead of one clobbering the other, and reuses `deliver_plan_message` (see
plan_delivery.py) so the resulting message stays the one canonical Plan
message for the week (#73) rather than a fresh one every time - the command
handler is just glue between three already-deep interfaces.

The recent-history dedup mirrors what `plan_pipeline.make_generate_plan_handler`
already does for the automatic path (ADR-0006: "дедуп по истории Тем всё равно
применим" applies to both inputs); unlike the automatic path's silent
skip-and-try-next-keyword, a rejected manual proposal gets an explicit reply,
since this is an interactive command rather than a batch job.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Protocol
from zoneinfo import ZoneInfo

from ..domain import PlanId, TopicDraft
from ..scheduling import week_label_for
from .gateway import TelegramGateway
from .plan_delivery import PlanMessageRefs, deliver_plan_message
from .types import PlanView

RECENT_HISTORY_DAYS = 90


class PlanProposal(PlanMessageRefs, Protocol):
    """The Plan operations a manual Topic proposal needs (see #10)."""

    async def recent_topic_titles(self, since: datetime) -> list[str]: ...

    async def add_topics(self, week_label: str, topics: list[TopicDraft]) -> PlanId: ...

    async def get(self, plan_id: PlanId) -> PlanView: ...


async def handle_topic_command(
    plan: PlanProposal,
    gateway: TelegramGateway,
    chat_id: int,
    text: str,
    *,
    tz: ZoneInfo,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> None:
    title = text.strip()
    if not title:
        await gateway.send_error(chat_id, "Укажите текст Темы: /topic <текст>")
        return

    current_time = now()
    since = current_time - timedelta(days=RECENT_HISTORY_DAYS)
    recent_titles = {existing.lower() for existing in await plan.recent_topic_titles(since)}
    if title.lower() in recent_titles:
        await gateway.send_error(chat_id, f"Тема «{title}» уже использовалась недавно.")
        return

    week_label = week_label_for(current_time, tz)
    plan_id = await plan.add_topics(week_label, [TopicDraft(title=title)])
    view = await plan.get(plan_id)
    await deliver_plan_message(plan, gateway, chat_id, view)
