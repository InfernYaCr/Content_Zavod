from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from content_zavod.domain import PlanId, PlanItemId, PlanItemView, PlanMessageRef, PlanView
from content_zavod.telegram import MessageGone
from content_zavod.telegram.generate_plan_command import (
    handle_cancel_regenerate_plan,
    handle_confirm_regenerate_plan,
    handle_generate_plan_command,
)

MOSCOW = ZoneInfo("Europe/Moscow")
FIXED_NOW = datetime(2026, 8, 3, 6, 0, tzinfo=UTC)  # 2026-W32 Monday


class FakePlan:
    def __init__(self, active: PlanView | None = None) -> None:
        self._active = active
        self.requested: list[str] = []
        self.requested_generation_ids: list[str] = []
        self.replacements: list[PlanId] = []
        self.archived: list[PlanId] = []
        self.message_refs: dict[PlanId, PlanMessageRef] = {}
        # idempotency key -> job id, like JobQueue.enqueue: the same key returns the same Job
        self.jobs_by_key: dict[str, int] = {}

    async def find_active(self, week_label: str) -> PlanView | None:
        return self._active

    async def get(self, plan_id: PlanId) -> PlanView:
        assert self._active is not None and self._active.id == plan_id
        return self._active

    async def archive(self, plan_id: PlanId) -> None:
        self.archived.append(plan_id)

    async def request_new(self, week_label: str, *, generation_id: str | None = None) -> int:
        if generation_id is None:
            self.requested.append(week_label)
        else:
            self.requested_generation_ids.append(generation_id)
        key = f"{week_label}:{generation_id}"
        return self.jobs_by_key.setdefault(key, len(self.jobs_by_key) + 1)

    async def get_message_ref(self, plan_id: PlanId) -> PlanMessageRef | None:
        return self.message_refs.get(plan_id)

    async def request_replacement(self, plan_id: PlanId) -> int:
        self.replacements.append(plan_id)
        return 1


class FakeQueue:
    """Job id -> status; a Job not listed is `queued`, like a fresh enqueue."""

    def __init__(self, statuses: dict[int, str] | None = None) -> None:
        self.statuses = dict(statuses or {})
        self.retried: list[int] = []

    async def get_status(self, job_id: int) -> str:
        return self.statuses.get(job_id, "queued")

    async def retry(self, job_id: int) -> bool:
        self.retried.append(job_id)
        self.statuses[job_id] = "queued"
        return True


class FakeGateway:
    def __init__(self) -> None:
        self.sent_notices: list[tuple[int, str]] = []
        self.sent_messages: list[tuple[int, str, object]] = []
        self.edited: list[tuple[int, int, str]] = []
        self.edited_plans: list[tuple[int, int, PlanView]] = []

    async def send_notice(self, chat_id, text) -> None:
        self.sent_notices.append((chat_id, text))

    async def send_message(self, chat_id, text, reply_markup=None) -> int:
        self.sent_messages.append((chat_id, text, reply_markup))
        return 1

    async def edit_notice(self, chat_id, message_id, text) -> None:
        self.edited.append((chat_id, message_id, text))

    async def edit_plan(self, chat_id, message_id, plan) -> None:
        self.edited_plans.append((chat_id, message_id, plan))


class FailingReplacementPlan(FakePlan):
    async def request_replacement(self, plan_id: PlanId) -> int:
        raise RuntimeError("queue unavailable")


TEAM_CHAT_ID = 1


async def _run(
    plan: FakePlan, gateway: FakeGateway, queue: FakeQueue | None = None, *, chat_id: int = 1
) -> None:
    await handle_generate_plan_command(
        plan,
        gateway,
        chat_id=chat_id,
        queue=queue or FakeQueue(),
        team_chat_id=TEAM_CHAT_ID,
        tz=MOSCOW,
        now=lambda: FIXED_NOW,
    )


@pytest.mark.asyncio
async def test_unannounced_run_starts_the_generation_without_a_notice() -> None:
    """The onboarding's «🚀 Запускаю!» card already says what happens (#96)."""
    plan, gateway = FakePlan(active=None), FakeGateway()

    await handle_generate_plan_command(
        plan,
        gateway,
        chat_id=5,
        queue=FakeQueue(),
        team_chat_id=TEAM_CHAT_ID,
        tz=MOSCOW,
        now=lambda: FIXED_NOW,
        announce=False,
    )

    assert plan.requested == ["2026-W32"]
    assert gateway.sent_notices == [] and gateway.sent_messages == []


@pytest.mark.asyncio
async def test_no_active_plan_requests_new_directly() -> None:
    plan, gateway, queue = FakePlan(active=None), FakeGateway(), FakeQueue()

    await _run(plan, gateway, queue)

    assert plan.requested == ["2026-W32"]
    assert queue.retried == []
    assert gateway.sent_notices == [(1, "Генерирую План на 3–9 августа 2026...")]
    assert gateway.sent_messages == []


@pytest.mark.asyncio
async def test_failed_week_job_is_retried_instead_of_silently_reused() -> None:
    """#83: the week's generation failed, so there's no Plan - the same idempotency key hands
    back the failed Job, which has to be retried rather than reported as running."""
    plan, gateway, queue = FakePlan(active=None), FakeGateway(), FakeQueue({1: "failed"})

    await _run(plan, gateway, queue)

    assert queue.retried == [1]
    assert plan.requested_generation_ids == []


@pytest.mark.asyncio
async def test_done_week_job_without_a_plan_starts_a_rerun() -> None:
    """#83/#84: a finished generation that left no Plan (empty result) gets a fresh rerun
    keyed on it - a repeated command collapses into that same rerun."""
    plan, gateway, queue = FakePlan(active=None), FakeGateway(), FakeQueue({1: "done"})

    await _run(plan, gateway, queue)
    await _run(plan, gateway, queue)

    assert plan.requested_generation_ids == ["rerun:1", "rerun:1"]
    assert len(plan.jobs_by_key) == 2
    assert queue.retried == []


@pytest.mark.asyncio
async def test_rerun_chain_continues_past_an_earlier_empty_rerun() -> None:
    plan, gateway = FakePlan(active=None), FakeGateway()
    queue = FakeQueue({1: "done", 2: "done"})

    await _run(plan, gateway, queue)

    assert plan.requested_generation_ids == ["rerun:1", "rerun:2"]


@pytest.mark.asyncio
async def test_called_outside_the_team_chat_points_to_the_team_chat() -> None:
    """#82: the Plan itself is delivered to the team chat by the notification handler."""
    plan, gateway = FakePlan(active=None), FakeGateway()

    await _run(plan, gateway, chat_id=77)

    assert gateway.sent_notices == [
        (77, "Генерирую План на 3–9 августа 2026. Он появится в чате команды.")
    ]


@pytest.mark.asyncio
async def test_active_plan_prompts_confirmation_instead_of_regenerating() -> None:
    active = PlanView(id=PlanId("plan-1"), week_label="2026-W32", items=[])
    plan, gateway = FakePlan(active=active), FakeGateway()

    await _run(plan, gateway)

    assert plan.requested == []
    assert len(gateway.sent_messages) == 1
    _chat_id, text, keyboard = gateway.sent_messages[0]
    assert "уже существует" in text
    assert "2026-W32" not in text and "3–9 августа 2026" in text
    assert keyboard is not None


@pytest.mark.asyncio
async def test_approved_plan_confirmation_warns_its_articles_go_to_the_archive() -> None:
    """#90: replacing an approved Plan archives it together with its Статьи - say so."""
    active = PlanView(
        id=PlanId("plan-1"),
        week_label="2026-W32",
        items=[PlanItemView(id=PlanItemId("item-1"), title="Тема", status="approved")],
    )
    plan, gateway = FakePlan(active=active), FakeGateway()

    await _run(plan, gateway)

    _chat_id, text, _keyboard = gateway.sent_messages[0]
    assert "уже утверждён" in text
    assert "Статьи" in text and "в архив" in text


@pytest.mark.asyncio
async def test_confirm_archives_old_plan_and_requests_a_new_one() -> None:
    active = PlanView(id=PlanId("plan-1"), week_label="2026-W32", items=[])
    plan, gateway = FakePlan(active=active), FakeGateway()

    await handle_confirm_regenerate_plan(
        plan, gateway, chat_id=1, message_id=5, plan_id=PlanId("plan-1")
    )

    assert plan.replacements == [PlanId("plan-1")]
    assert gateway.edited == [(1, 5, "Генерирую новый План...")]
    assert gateway.edited_plans == []


@pytest.mark.asyncio
async def test_confirm_re_renders_the_replaced_plans_message() -> None:
    """#81: the replaced Plan's own message is re-rendered from the DB (archived, no buttons)."""
    active = PlanView(id=PlanId("plan-1"), week_label="2026-W32", items=[])
    plan, gateway = FakePlan(active=active), FakeGateway()
    plan.message_refs[PlanId("plan-1")] = PlanMessageRef(chat_id=-100, message_id=9)

    await handle_confirm_regenerate_plan(
        plan, gateway, chat_id=1, message_id=5, plan_id=PlanId("plan-1")
    )

    assert gateway.edited_plans == [(-100, 9, active)]


@pytest.mark.asyncio
async def test_confirm_skips_the_replaced_plans_message_if_it_was_deleted() -> None:
    """#106: an archived Plan whose message someone deleted has nothing left to redraw - the
    confirmation still goes through, and no new message is sent for a Plan that's gone."""
    active = PlanView(id=PlanId("plan-1"), week_label="2026-W32", items=[])
    plan, gateway = FakePlan(active=active), FakeGateway()
    plan.message_refs[PlanId("plan-1")] = PlanMessageRef(chat_id=-100, message_id=9)

    async def gone(chat_id, message_id, view) -> None:
        raise MessageGone(chat_id, message_id)

    gateway.edit_plan = gone  # type: ignore[method-assign]

    await handle_confirm_regenerate_plan(
        plan, gateway, chat_id=1, message_id=5, plan_id=PlanId("plan-1")
    )

    assert plan.replacements == [PlanId("plan-1")]
    assert gateway.edited == [(1, 5, "Генерирую новый План...")]
    assert gateway.sent_messages == []


@pytest.mark.asyncio
async def test_confirm_does_not_report_success_or_archive_through_the_old_api_when_enqueue_fails() -> (
    None
):
    active = PlanView(id=PlanId("plan-1"), week_label="2026-W32", items=[])
    plan, gateway = FailingReplacementPlan(active=active), FakeGateway()

    with pytest.raises(RuntimeError, match="queue unavailable"):
        await handle_confirm_regenerate_plan(
            plan, gateway, chat_id=1, message_id=5, plan_id=PlanId("plan-1")
        )

    assert plan.archived == []
    assert gateway.edited == []


@pytest.mark.asyncio
async def test_cancel_leaves_the_plan_untouched() -> None:
    plan, gateway = FakePlan(), FakeGateway()

    await handle_cancel_regenerate_plan(gateway, chat_id=1, message_id=5)

    assert plan.archived == []
    assert plan.requested == []
    assert gateway.edited == [(1, 5, "Отменено.")]
