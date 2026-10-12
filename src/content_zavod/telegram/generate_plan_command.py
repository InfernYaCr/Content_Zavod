"""handle_generate_plan_command: the manual /generate_plan command (see scheduling/weekly_plan_trigger.py).

It enqueues the same idempotent `Plan.request_new` the Расписание tick calls, so a manual run
and the schedule racing each other collapse into one Job. If the week already has an active
Plan, asks for confirmation before archiving it and starting a fresh one, so a mis-tap can't
silently discard a Plan mid-review - and says outright when that Plan is already approved,
since its Статьи go to the archive with it (#90).

With no active Plan, the week's Job is made to actually run again (#83):
`request_new` hands back the week's existing Job by its idempotency key, so a
`failed` one is retried, and a `done` one that left no active Plan behind (an
empty result, see #84) is followed by a fresh rerun keyed on it. The Plan itself lands in the
Пространство owner's private chat via the notification handler. The week is counted in the
Workspace's own time zone, like the Расписание tick does (ADR-0015). Replies show the week as
a date range, never the raw `2026-W41` label.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Protocol
from zoneinfo import ZoneInfo

from ..domain import PlanId
from ..job_queue import JobId, JobStatus
from ..scheduling import week_label_for
from ..workspaces import Workspace, WorkspaceId
from .gateway import MessageGone, TelegramGateway, build_confirm_keyboard, format_week_range
from .types import PlanMessageRef, PlanSummary, PlanView


class PlanGeneration(Protocol):
    async def find_active(self, workspace_id: WorkspaceId, week_label: str) -> PlanView | None: ...

    async def get(self, workspace_id: WorkspaceId, plan_id: PlanId) -> PlanView: ...

    async def get_summary(self, workspace_id: WorkspaceId, plan_id: PlanId) -> PlanSummary: ...

    async def archive(self, workspace_id: WorkspaceId, plan_id: PlanId) -> None: ...

    async def request_new(
        self, workspace_id: WorkspaceId, week_label: str, *, generation_id: str | None = None
    ) -> JobId: ...

    async def request_replacement(self, workspace_id: WorkspaceId, plan_id: PlanId) -> object: ...

    async def get_message_ref(
        self, workspace_id: WorkspaceId, plan_id: PlanId
    ) -> PlanMessageRef | None: ...


class GenerationJobs(Protocol):
    """The JobQueue calls needed to restart the week's `generate_plan` Job (#83)."""

    async def get_status(self, workspace_id: WorkspaceId, job_id: JobId) -> JobStatus: ...

    async def retry(self, workspace_id: WorkspaceId, job_id: JobId) -> bool: ...


def workspace_week(workspace: Workspace, now: datetime) -> str:
    """This week's label in the Пространство's own time zone - the one its Расписание uses."""
    return week_label_for(now, ZoneInfo(workspace.timezone))


async def handle_generate_plan_command(
    plan: PlanGeneration,
    gateway: TelegramGateway,
    workspace: Workspace,
    chat_id: int,
    *,
    queue: GenerationJobs,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
    announce: bool = True,
) -> None:
    """`announce=False`: the caller has already told the user what is happening (the
    onboarding's «🚀 Запускаю!», #96), so a started generation adds no «Генерирую План…»."""
    week_label = workspace_week(workspace, now())
    week = format_week_range(week_label)
    active = await plan.find_active(workspace.id, week_label)
    if active is None:
        await _run_week_generation(plan, queue, workspace.id, week_label)
        if announce:
            await gateway.send_notice(chat_id, f"Генерирую План на {week}...")
        return
    text = f"План на {week} уже существует. Перегенерировать полностью?"
    if any(item.status == "approved" for item in active.items):
        text = (
            f"⚠️ План на {week} уже утверждён. Если перегенерировать, он и его Статьи "
            "уйдут в архив (останутся в /history). Перегенерировать полностью?"
        )
    await gateway.send_message(chat_id, text, reply_markup=build_confirm_keyboard(active.id))


async def _run_week_generation(
    plan: PlanGeneration, queue: GenerationJobs, workspace_id: WorkspaceId, week_label: str
) -> None:
    """Makes sure a `generate_plan` Job for the week is queued or running (#83). A `done` Job
    found here produced no active Plan, so the next link of a rerun chain is enqueued - keyed
    on the Job it reruns, so a repeated /generate_plan collapses into the same rerun rather
    than starting a second one."""
    # ponytail: a `done` Job whose notification hasn't been applied yet (~1s poll) or a
    # replacement still in flight (`request_replacement` is off this chain) is read as "no
    # Plan" too and gets a rerun - worst case one extra generation appending to the same
    # draft Plan. Look up the week's latest generate_plan Job instead if that bites.
    job_id = await plan.request_new(workspace_id, week_label)
    status = await queue.get_status(workspace_id, job_id)
    while status == "done":
        job_id = await plan.request_new(workspace_id, week_label, generation_id=f"rerun:{job_id}")
        status = await queue.get_status(workspace_id, job_id)
    if status == "failed":
        await queue.retry(workspace_id, job_id)


async def handle_confirm_regenerate_plan(
    plan: PlanGeneration,
    gateway: TelegramGateway,
    workspace_id: WorkspaceId,
    chat_id: int,
    message_id: int,
    plan_id: PlanId,
) -> None:
    """Also re-renders the replaced Plan's own message from the DB (#81), so it reads
    "в архиве" with no buttons left to press instead of keeping stale ones. One deleted in
    the chat (#106) is simply left gone: an archived Plan needs no replacement message."""
    await plan.request_replacement(workspace_id, plan_id)
    await gateway.edit_notice(chat_id, message_id, "Генерирую новый План...")
    ref = await plan.get_message_ref(workspace_id, plan_id)
    if ref is not None:
        try:
            await gateway.edit_plan(
                ref.chat_id, ref.message_id, await plan.get(workspace_id, plan_id)
            )
        except MessageGone:
            pass


async def handle_cancel_regenerate_plan(
    plan: PlanGeneration,
    gateway: TelegramGateway,
    workspace_id: WorkspaceId,
    chat_id: int,
    message_id: int,
    plan_id: PlanId,
) -> None:
    """«Отмена» under «Перегенерировать полностью?». The Plan is looked up first: a button
    naming a Plan this Пространство doesn't have is a stale one (ADR-0015)."""
    await plan.get_summary(workspace_id, plan_id)
    await gateway.edit_notice(chat_id, message_id, "Отменено.")
