from __future__ import annotations

from datetime import UTC, datetime

import pytest

from content_zavod.domain import (
    PlanId,
    PlanItemId,
    PlanItemView,
    PlanMessageRef,
    PlanNotFound,
    PlanSummary,
    PlanView,
)
from content_zavod.settings import OwnerSettings
from content_zavod.telegram import MessageGone
from content_zavod.telegram.generate_plan_command import (
    handle_cancel_regenerate_plan,
    handle_confirm_regenerate_plan,
    handle_generate_plan_command,
)
from content_zavod.workspaces import Workspace
from tests.tenancy import WS_A

FIXED_NOW = datetime(2026, 8, 3, 6, 0, tzinfo=UTC)  # 2026-W32 Monday, 09:00 in Moscow


class FakeSettings:
    def __init__(self, *, niche_saved: bool = True) -> None:
        self.niche_saved = niche_saved

    async def read(self, workspace_id: int) -> OwnerSettings:
        return OwnerSettings(
            niche="кофейни",
            directions=(),
            persona=None,
            custom_persona=None,
            niche_saved=self.niche_saved,
        )


WORKSPACE = Workspace(WS_A, 1, None, "Europe/Moscow", None, None)


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

    async def find_active(self, workspace_id: int, week_label: str) -> PlanView | None:
        return self._active

    async def get(self, workspace_id: int, plan_id: PlanId) -> PlanView:
        assert self._active is not None and self._active.id == plan_id
        return self._active

    async def archive(self, workspace_id: int, plan_id: PlanId) -> None:
        self.archived.append(plan_id)

    async def request_new(
        self, workspace_id: int, week_label: str, *, generation_id: str | None = None
    ) -> int:
        if generation_id is None:
            self.requested.append(week_label)
        else:
            self.requested_generation_ids.append(generation_id)
        key = f"{week_label}:{generation_id}"
        return self.jobs_by_key.setdefault(key, len(self.jobs_by_key) + 1)

    async def get_message_ref(self, workspace_id: int, plan_id: PlanId) -> PlanMessageRef | None:
        return self.message_refs.get(plan_id)

    async def get_summary(self, workspace_id: int, plan_id: PlanId) -> PlanSummary:
        if self._active is None or self._active.id != plan_id:
            raise PlanNotFound(plan_id)
        return PlanSummary(id=plan_id, week_label="2026-W32", status="pending_review")

    async def request_replacement(self, workspace_id: int, plan_id: PlanId) -> int:
        self.replacements.append(plan_id)
        return 1


class FakeQueue:
    """Job id -> status; a Job not listed is `queued`, like a fresh enqueue."""

    def __init__(self, statuses: dict[int, str] | None = None) -> None:
        self.statuses = dict(statuses or {})
        self.retried: list[int] = []

    async def get_status(self, workspace_id: int, job_id: int) -> str:
        return self.statuses.get(job_id, "queued")

    async def retry(self, workspace_id: int, job_id: int) -> bool:
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
    async def request_replacement(self, workspace_id: int, plan_id: PlanId) -> int:
        raise RuntimeError("queue unavailable")


async def _run(
    plan: FakePlan,
    gateway: FakeGateway,
    queue: FakeQueue | None = None,
    *,
    workspace: Workspace = WORKSPACE,
    now: datetime = FIXED_NOW,
    niche_saved: bool = True,
) -> None:
    await handle_generate_plan_command(
        plan,
        gateway,
        workspace,
        1,
        queue=queue or FakeQueue(),
        settings=FakeSettings(niche_saved=niche_saved),
        now=lambda: now,
    )


@pytest.mark.asyncio
async def test_unannounced_run_starts_the_generation_without_a_notice() -> None:
    """The onboarding's «🚀 Запускаю!» card already says what happens (#96)."""
    plan, gateway = FakePlan(active=None), FakeGateway()

    await handle_generate_plan_command(
        plan,
        gateway,
        WORKSPACE,
        5,
        queue=FakeQueue(),
        settings=FakeSettings(),
        now=lambda: FIXED_NOW,
        announce=False,
    )

    assert plan.requested == ["2026-W32"]
    assert gateway.sent_notices == [] and gateway.sent_messages == []


@pytest.mark.asyncio
async def test_without_a_niche_it_asks_for_one_instead_of_drafting_by_the_default() -> None:
    """#118: a Plan by the default Ниша («маркетинг») would be about someone else's blog."""
    plan, gateway = FakePlan(active=None), FakeGateway()

    await _run(plan, gateway, niche_saved=False)

    assert plan.requested == [] and gateway.sent_notices == []
    ((chat_id, text, keyboard),) = gateway.sent_messages
    assert chat_id == 1 and text.startswith("Сначала скажите, о чём ваш блог")
    ((button,),) = keyboard.inline_keyboard
    assert (button.text, button.callback_data) == ("✏️ Указать Нишу", "sa:niche")


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
async def test_the_week_is_counted_in_the_workspaces_time_zone() -> None:
    """ADR-0015: Sunday 20:00 UTC is already Monday in Vladivostok - its Расписание's next
    week, not Moscow's current one."""
    plan, gateway = FakePlan(active=None), FakeGateway()
    far_east = Workspace(WS_A, 1, None, "Asia/Vladivostok", None, None)
    sunday_night = datetime(2026, 8, 9, 20, 0, tzinfo=UTC)

    await _run(plan, gateway, workspace=far_east, now=sunday_night)
    await _run(plan, gateway, now=sunday_night)

    assert plan.requested == ["2026-W33", "2026-W32"]


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
        plan, gateway, WS_A, chat_id=1, message_id=5, plan_id=PlanId("plan-1")
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
        plan, gateway, WS_A, chat_id=1, message_id=5, plan_id=PlanId("plan-1")
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
        plan, gateway, WS_A, chat_id=1, message_id=5, plan_id=PlanId("plan-1")
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
            plan, gateway, WS_A, chat_id=1, message_id=5, plan_id=PlanId("plan-1")
        )

    assert plan.archived == []
    assert gateway.edited == []


@pytest.mark.asyncio
async def test_cancel_leaves_the_plan_untouched() -> None:
    active = PlanView(id=PlanId("plan-1"), week_label="2026-W32", items=[])
    plan, gateway = FakePlan(active=active), FakeGateway()

    await handle_cancel_regenerate_plan(gateway, chat_id=1, message_id=5)

    assert plan.archived == []
    assert plan.requested == []
    assert gateway.edited == [(1, 5, "Отменено.")]
