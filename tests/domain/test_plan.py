from datetime import datetime, timedelta, timezone

import asyncpg
import pytest

from content_zavod.domain import (
    Plan,
    PlanId,
    PlanItemNotEditable,
    PlanItemNotFound,
    PlanNotFound,
    PlanView,
    TopicDraft,
)
from content_zavod.domain.plan import PlanItemDetail
from content_zavod.job_queue import JobQueue


async def _create_plan(
    plan: Plan, *, titles: tuple[str, ...] = ("Topic A", "Topic B")
) -> tuple[PlanId, PlanView]:
    plan_id = await plan.add_topics("Week 1", [TopicDraft(title=t) for t in titles])
    view = await plan.get(plan_id)
    return plan_id, view


async def test_create_and_get_round_trip(plan: Plan) -> None:
    plan_id, view = await _create_plan(plan)

    assert view.id == plan_id
    assert view.week_label == "Week 1"
    assert [item.title for item in view.items] == ["Topic A", "Topic B"]
    assert all(item.status == "pending_review" for item in view.items)


async def test_get_raises_for_unknown_plan(plan: Plan) -> None:
    with pytest.raises(PlanNotFound):
        await plan.get("missing")


async def test_get_item_returns_full_detail(plan: Plan) -> None:
    plan_id = await plan.add_topics(
        "Week 1", [TopicDraft(title="Topic A", summary="summary A", keywords=["kw1", "kw2"])]
    )
    view = await plan.get(plan_id)
    item_id = view.items[0].id

    detail = await plan.get_item(item_id)

    assert detail == PlanItemDetail(id=item_id, title="Topic A", summary="summary A", keywords=["kw1", "kw2"])


async def test_get_item_raises_for_unknown_item(plan: Plan) -> None:
    with pytest.raises(PlanItemNotFound):
        await plan.get_item("missing")


async def test_delete_item_marks_it_rejected(plan: Plan) -> None:
    _, view = await _create_plan(plan)
    item_id = view.items[0].id

    await plan.delete_item(item_id)

    updated = await plan.get(view.id)
    statuses = {item.id: item.status for item in updated.items}
    assert statuses[item_id] == "rejected"
    assert statuses[view.items[1].id] == "pending_review"


async def test_delete_item_is_idempotent(plan: Plan) -> None:
    _, view = await _create_plan(plan)
    item_id = view.items[0].id

    await plan.delete_item(item_id)
    await plan.delete_item(item_id)  # no-op, must not raise

    updated = await plan.get(view.id)
    assert updated.items[0].status == "rejected"


async def test_delete_item_raises_for_unknown_item(plan: Plan) -> None:
    with pytest.raises(PlanItemNotFound):
        await plan.delete_item("missing")


async def test_delete_item_raises_once_approved(plan: Plan) -> None:
    _, view = await _create_plan(plan)

    await plan.approve_all(view.id)

    with pytest.raises(PlanItemNotEditable):
        await plan.delete_item(view.items[0].id)


async def test_approve_all_approves_pending_items_and_leaves_rejected_alone(plan: Plan) -> None:
    _, view = await _create_plan(plan)
    await plan.delete_item(view.items[0].id)

    await plan.approve_all(view.id)

    updated = await plan.get(view.id)
    statuses = {item.id: item.status for item in updated.items}
    assert statuses[view.items[0].id] == "rejected"
    assert statuses[view.items[1].id] == "approved"


async def test_approve_all_is_idempotent(plan: Plan) -> None:
    _, view = await _create_plan(plan)

    await plan.approve_all(view.id)
    await plan.approve_all(view.id)  # no-op, must not raise

    updated = await plan.get(view.id)
    assert all(item.status == "approved" for item in updated.items)


async def test_approve_all_raises_for_unknown_plan(plan: Plan) -> None:
    with pytest.raises(PlanNotFound):
        await plan.approve_all("missing")


async def test_regenerate_item_enqueues_a_job_instead_of_calling_an_llm_directly(
    plan: Plan, queue: JobQueue
) -> None:
    _, view = await _create_plan(plan)
    item_id = view.items[0].id

    await plan.regenerate_item(item_id, comment="make it punchier")

    claimed = await queue.claim_next()
    assert claimed is not None
    assert claimed.job_type == "regenerate_topic"
    assert claimed.payload == {"plan_item_id": item_id, "comment": "make it punchier"}


async def test_regenerate_item_retried_before_any_state_change_does_not_duplicate_the_job(
    plan: Plan, queue: JobQueue
) -> None:
    _, view = await _create_plan(plan)
    item_id = view.items[0].id

    await plan.regenerate_item(item_id, comment="please")
    await plan.regenerate_item(item_id, comment="please")  # e.g. a retried Telegram callback

    first_claim = await queue.claim_next()
    assert first_claim is not None
    second_claim = await queue.claim_next()
    assert second_claim is None


async def test_regenerate_item_raises_once_approved(plan: Plan) -> None:
    _, view = await _create_plan(plan)
    await plan.approve_all(view.id)

    with pytest.raises(PlanItemNotEditable):
        await plan.regenerate_item(view.items[0].id, comment=None)


async def test_apply_regeneration_updates_the_pending_item(plan: Plan) -> None:
    _, view = await _create_plan(plan)
    item_id = view.items[0].id

    await plan.apply_regeneration(item_id, TopicDraft(title="Better Title", summary="new summary"))

    updated = await plan.get(view.id)
    assert updated.items[0].title == "Better Title"
    assert updated.items[0].status == "pending_review"


async def test_apply_regeneration_ignores_a_stale_result_after_approval(plan: Plan) -> None:
    _, view = await _create_plan(plan)
    item_id = view.items[0].id
    await plan.approve_all(view.id)

    await plan.apply_regeneration(item_id, TopicDraft(title="Too Late"))

    updated = await plan.get(view.id)
    assert updated.items[0].title == "Topic A"
    assert updated.items[0].status == "approved"


async def test_recent_topic_titles_returns_titles_created_since(plan: Plan) -> None:
    before = datetime.now(timezone.utc) - timedelta(minutes=1)
    await _create_plan(plan, titles=("Fresh Topic",))

    titles = await plan.recent_topic_titles(since=before)

    assert "Fresh Topic" in titles


async def test_recent_topic_titles_excludes_titles_older_than_since(plan: Plan) -> None:
    await _create_plan(plan, titles=("Old Topic",))
    after = datetime.now(timezone.utc) + timedelta(minutes=1)

    titles = await plan.recent_topic_titles(since=after)

    assert "Old Topic" not in titles


async def test_add_topics_appends_to_the_existing_draft_for_a_repeated_week_label(
    plan: Plan,
) -> None:
    first_id = await plan.add_topics("Week 1", [TopicDraft(title="Topic A")])

    second_id = await plan.add_topics("Week 1", [TopicDraft(title="Topic B")])

    assert second_id == first_id
    view = await plan.get(first_id)
    assert [item.title for item in view.items] == ["Topic A", "Topic B"]


async def test_add_topics_skips_a_title_already_present_in_the_plan(plan: Plan) -> None:
    # e.g. a redelivered generate_plan notification re-appending the same topics
    first_id = await plan.add_topics("Week 1", [TopicDraft(title="Topic A")])

    second_id = await plan.add_topics(
        "Week 1", [TopicDraft(title="Topic A"), TopicDraft(title="Topic B")]
    )

    assert second_id == first_id
    view = await plan.get(first_id)
    assert [item.title for item in view.items] == ["Topic A", "Topic B"]


async def test_add_topics_starts_a_fresh_draft_once_the_prior_plan_is_approved(
    plan: Plan,
) -> None:
    first_id, view = await _create_plan(plan, titles=("Topic A",))
    await plan.approve_all(view.id)

    second_id = await plan.add_topics("Week 1", [TopicDraft(title="Topic B")])

    assert second_id != first_id
    second_view = await plan.get(second_id)
    assert [item.title for item in second_view.items] == ["Topic B"]


async def test_request_new_enqueues_a_generate_plan_job_for_the_given_week(
    plan: Plan, queue: JobQueue
) -> None:
    await plan.request_new("2026-W32")

    claimed = await queue.claim_next()
    assert claimed is not None
    assert claimed.job_type == "generate_plan"
    assert claimed.payload == {"week_label": "2026-W32"}


async def test_request_new_retried_for_the_same_week_does_not_duplicate_the_job(
    plan: Plan, queue: JobQueue
) -> None:
    await plan.request_new("2026-W32")
    await plan.request_new("2026-W32")  # e.g. missed-run catch-up racing the manual command

    first_claim = await queue.claim_next()
    assert first_claim is not None
    second_claim = await queue.claim_next()
    assert second_claim is None


async def test_request_cover_enqueues_a_job_with_the_items_title_and_summary(
    plan: Plan, queue: JobQueue
) -> None:
    plan_id = await plan.add_topics("Week 1", [TopicDraft(title="Topic A", summary="a summary")])
    view = await plan.get(plan_id)
    item_id = view.items[0].id

    await plan.request_cover(item_id)

    claimed = await queue.claim_next()
    assert claimed is not None
    assert claimed.job_type == "generate_cover"
    assert claimed.payload == {"plan_item_id": item_id, "title": "Topic A", "summary": "a summary"}


async def test_request_cover_retried_before_any_state_change_does_not_duplicate_the_job(
    plan: Plan, queue: JobQueue
) -> None:
    plan_id = await plan.add_topics("Week 1", [TopicDraft(title="Topic A")])
    view = await plan.get(plan_id)
    item_id = view.items[0].id

    await plan.request_cover(item_id)
    await plan.request_cover(item_id)

    first_claim = await queue.claim_next()
    assert first_claim is not None
    second_claim = await queue.claim_next()
    assert second_claim is None


async def test_request_cover_raises_for_unknown_item(plan: Plan) -> None:
    with pytest.raises(PlanItemNotFound):
        await plan.request_cover("missing")


async def test_find_active_returns_pending_or_approved_plan_for_the_week(plan: Plan) -> None:
    plan_id, _ = await _create_plan(plan)

    found = await plan.find_active("Week 1")

    assert found is not None
    assert found.id == plan_id


async def test_find_active_returns_none_when_no_plan_exists_for_the_week(plan: Plan) -> None:
    found = await plan.find_active("Week 1")

    assert found is None


async def test_find_active_ignores_an_archived_plan(plan: Plan) -> None:
    plan_id, _ = await _create_plan(plan)
    await plan.archive(plan_id)

    found = await plan.find_active("Week 1")

    assert found is None


async def test_archive_marks_plan_and_pending_items_archived(plan: Plan) -> None:
    plan_id, view = await _create_plan(plan)

    await plan.archive(plan_id)

    updated = await plan.get(plan_id)
    assert all(item.status == "archived" for item in updated.items)
    found = await plan.find_active("Week 1")
    assert found is None


async def test_archive_leaves_rejected_items_rejected(plan: Plan) -> None:
    _, view = await _create_plan(plan)
    await plan.delete_item(view.items[0].id)

    await plan.archive(view.id)

    updated = await plan.get(view.id)
    statuses = {item.id: item.status for item in updated.items}
    assert statuses[view.items[0].id] == "rejected"
    assert statuses[view.items[1].id] == "archived"


async def test_archive_is_idempotent(plan: Plan) -> None:
    plan_id, _ = await _create_plan(plan)

    await plan.archive(plan_id)
    await plan.archive(plan_id)  # no-op, must not raise

    updated = await plan.get(plan_id)
    assert all(item.status == "archived" for item in updated.items)


async def test_archive_raises_for_unknown_plan(plan: Plan) -> None:
    with pytest.raises(PlanNotFound):
        await plan.archive("missing")


async def test_apply_cover_persists_image_and_mime_type(plan: Plan, pool: asyncpg.Pool) -> None:
    plan_id = await plan.add_topics("Week 1", [TopicDraft(title="Topic A")])
    view = await plan.get(plan_id)
    item_id = view.items[0].id

    await plan.apply_cover(item_id, b"fake-image-bytes", "image/jpeg")

    row = await pool.fetchrow(
        "SELECT cover_image, cover_mime_type, cover_generated_at FROM plan_items WHERE id = $1",
        item_id,
    )
    assert bytes(row["cover_image"]) == b"fake-image-bytes"
    assert row["cover_mime_type"] == "image/jpeg"
    assert row["cover_generated_at"] is not None
