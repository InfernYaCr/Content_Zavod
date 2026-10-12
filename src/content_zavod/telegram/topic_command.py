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

The Plan message goes to the Пространство owner's private chat, where every other delivery of
the Workspace goes (ADR-0015); the week is counted in the Workspace's own time zone, like the
Расписание tick does.

A Тема is the Owner's text in their own generations' prompts, so it is capped at
`TOPIC_MAX_LENGTH` (ADR-0015, «Безопасность»): a longer one is refused and nothing is added.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Protocol

from ..domain import PlanId, TopicDraft
from ..workspaces import Workspace, WorkspaceId
from .gateway import TelegramGateway
from .generate_plan_command import workspace_week
from .plan_delivery import PlanMessageRefs, deliver_plan_message
from .texts import TOPIC_ADDED_TEXT, TOPIC_TOO_LONG_TEXT
from .types import PlanView

RECENT_HISTORY_DAYS = 90
TOPIC_MAX_LENGTH = 300


class PlanProposal(PlanMessageRefs, Protocol):
    """The Plan operations a manual Topic proposal needs (see #10)."""

    async def recent_topic_titles(
        self, workspace_id: WorkspaceId, since: datetime
    ) -> list[str]: ...

    async def add_topics(
        self, workspace_id: WorkspaceId, week_label: str, topics: list[TopicDraft]
    ) -> PlanId: ...

    async def get(self, workspace_id: WorkspaceId, plan_id: PlanId) -> PlanView: ...


async def handle_topic_command(
    plan: PlanProposal,
    gateway: TelegramGateway,
    workspace: Workspace,
    chat_id: int,
    text: str,
    *,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> None:
    title = text.strip()
    if not title:
        await gateway.send_error(chat_id, "Укажите текст Темы: /topic <текст>")
        return
    if len(title) > TOPIC_MAX_LENGTH:
        await gateway.send_error(chat_id, TOPIC_TOO_LONG_TEXT.format(limit=TOPIC_MAX_LENGTH))
        return

    current_time = now()
    since = current_time - timedelta(days=RECENT_HISTORY_DAYS)
    recent_titles = {
        existing.lower() for existing in await plan.recent_topic_titles(workspace.id, since)
    }
    if title.lower() in recent_titles:
        await gateway.send_error(chat_id, f"Тема «{title}» уже использовалась недавно.")
        return

    week_label = workspace_week(workspace, current_time)
    plan_id = await plan.add_topics(workspace.id, week_label, [TopicDraft(title=title)])
    view = await plan.get(workspace.id, plan_id)
    sent_new = await deliver_plan_message(
        plan, gateway, workspace.id, workspace.owner_telegram_id, view
    )
    if not sent_new:
        # The Plan message was edited in place, maybe far up the chat - without a reply the
        # /topic would look like it did nothing.
        await gateway.send_notice(chat_id, TOPIC_ADDED_TEXT)
