"""Unit tests for bot_main's notification-dispatch glue (`_make_notification_handler`).

Everything else in entrypoints/bot.py is aiogram/Postgres wiring, verified by
local manual runs per issue #13's acceptance criteria - this is the one piece
of non-trivial logic (routing a JobResult to the right domain call + gateway
render) worth a fast unit test.
"""

from __future__ import annotations

import base64
import logging

import pytest

from content_zavod.domain import (
    ArticleSummary,
    ArticleView,
    GeneratedVersion,
    PlanHubView,
    PlanId,
    PlanItemId,
    PlanMessageRef,
    PlanView,
    TopicDraft,
)
from content_zavod.domain.plan import PlanItemDetail
from content_zavod.entrypoints.bot import _make_notification_handler
from content_zavod.job_queue import JobResult


class FakePlan:
    def __init__(self) -> None:
        self.added_topics: list[tuple[str, list[TopicDraft]]] = []
        self.applied_regenerations: list[tuple[str, TopicDraft]] = []
        self.applied_covers: list[tuple[str, bytes, str]] = []
        self.message_refs: dict[str, PlanMessageRef] = {}
        self.plan_id_for_item = PlanId("plan-1")
        self.plan_status = "approved"
        self.manual_cover_jobs: set[int] = set()
        self.cover_failures_marked: list[int] = []
        self.mark_cover_generation_failed_returns: PlanItemId | None = PlanItemId("item-1")

    async def add_topics(self, week_label: str, topics: list[TopicDraft]) -> PlanId:
        self.added_topics.append((week_label, topics))
        return PlanId("plan-1")

    async def get(self, plan_id: PlanId) -> PlanView:
        return PlanView(id=plan_id, week_label="Week 1", items=[])

    async def get_item(self, plan_item_id: PlanItemId) -> PlanItemDetail:
        return PlanItemDetail(id=plan_item_id, title="Topic A", summary="s", keywords=[])

    async def apply_regeneration(self, plan_item_id: str, topic: TopicDraft) -> None:
        self.applied_regenerations.append((plan_item_id, topic))

    async def apply_cover(self, plan_item_id: str, image: bytes, mime_type: str) -> None:
        self.applied_covers.append((plan_item_id, image, mime_type))

    async def get_message_ref(self, plan_id: PlanId) -> PlanMessageRef | None:
        return self.message_refs.get(plan_id)

    async def record_message_ref(self, plan_id: PlanId, chat_id: int, message_id: int) -> None:
        self.message_refs.setdefault(
            plan_id, PlanMessageRef(chat_id=chat_id, message_id=message_id)
        )

    async def get_plan_id_for_item(self, plan_item_id: PlanItemId) -> PlanId:
        return self.plan_id_for_item

    async def mark_cover_generation_failed(self, source_job_id: int) -> PlanItemId | None:
        self.cover_failures_marked.append(source_job_id)
        return self.mark_cover_generation_failed_returns

    async def is_manual_cover_job(self, job_id: int) -> bool:
        return job_id in self.manual_cover_jobs

    async def get_hub(self, plan_id: PlanId) -> PlanHubView:
        return PlanHubView(id=plan_id, week_label="2026-W41", status=self.plan_status, topics=[])


class FakeArticle:
    def __init__(self) -> None:
        self.recorded_versions: list[tuple[str, GeneratedVersion]] = []
        self.failed_jobs: list[int] = []
        self.application = "applied"
        self.plan_id_for_article = PlanId("plan-1")
        self.articles_for_plan: list[ArticleView] = []

    async def record_version(self, article_id: str, version: GeneratedVersion) -> str:
        self.recorded_versions.append((article_id, version))
        return self.application

    async def mark_generation_failed(self, source_job_id: int) -> str | None:
        self.failed_jobs.append(source_job_id)
        return "article-1"

    async def get(self, article_id: str) -> ArticleView:
        return ArticleView(
            id=article_id, plan_item_id="item-1", title="T", platform="P", content=b"c"
        )

    async def get_summary(self, article_id: str) -> ArticleSummary:
        return ArticleSummary(id=article_id, title="Topic A", platform="vc", status="error")

    async def get_plan_id(self, article_id: str) -> PlanId:
        return self.plan_id_for_article

    async def list_for_plan(self, plan_id: PlanId) -> list[ArticleView]:
        return self.articles_for_plan


class FakeGateway:
    def __init__(self) -> None:
        self.sent_plans: list[tuple[int, PlanView]] = []
        self.edited_plans: list[tuple[int, int, PlanView]] = []
        self.sent_articles: list[tuple[int, ArticleView]] = []
        self.article_read_urls: list[str | None] = []
        self.sent_errors: list[tuple[int, str]] = []
        self.sent_errors_with_retry: list[tuple[int, str, int]] = []
        self.sent_notices: list[tuple[int, str]] = []
        self.sent_covers: list[tuple[int, bytes, str, str]] = []
        # Every Хаб send/edit as (chat_id, message_id or None for a send, plan_id).
        self.hub_redraws: list[tuple[int, int | None, str]] = []
        self._next_message_id = 0

    async def send_hub(self, chat_id: int, hub: PlanHubView) -> int:
        self._next_message_id += 1
        self.hub_redraws.append((chat_id, None, hub.id))
        return self._next_message_id

    async def edit_hub(self, chat_id: int, message_id: int, hub: PlanHubView) -> None:
        self.hub_redraws.append((chat_id, message_id, hub.id))

    async def send_plan(self, chat_id: int, plan: PlanView) -> int:
        self.sent_plans.append((chat_id, plan))
        self._next_message_id += 1
        return self._next_message_id

    async def edit_plan(self, chat_id: int, message_id: int, plan: PlanView) -> None:
        self.edited_plans.append((chat_id, message_id, plan))

    async def send_article_ready(
        self, chat_id: int, article: ArticleView, *, read_url: str | None = None
    ) -> None:
        self.sent_articles.append((chat_id, article))
        self.article_read_urls.append(read_url)

    async def send_error(self, chat_id: int, text: str) -> None:
        self.sent_errors.append((chat_id, text))

    async def send_error_with_retry(self, chat_id: int, text: str, job_id: int) -> None:
        self.sent_errors_with_retry.append((chat_id, text, job_id))

    async def send_notice(self, chat_id: int, text: str) -> None:
        self.sent_notices.append((chat_id, text))

    async def send_cover(self, chat_id: int, image: bytes, mime_type: str, title: str) -> None:
        self.sent_covers.append((chat_id, image, mime_type, title))


async def test_failed_job_sends_error_with_retry_button() -> None:
    plan, article, gateway = FakePlan(), FakeArticle(), FakeGateway()
    handle = _make_notification_handler(plan, article, gateway, 42)

    await handle(JobResult(job_id=1, job_type="generate_plan", status="failed", error="boom"))

    assert gateway.sent_errors_with_retry == [(42, "Не удалось составить План.", 1)]


@pytest.mark.parametrize(
    ("job_type", "text"),
    [
        ("regenerate_topic", "Не удалось перегенерировать Тему."),
        ("regenerate_article", "Не удалось переписать Статью для VC.ru: «Topic A»"),
        ("some_future_job", "Не удалось выполнить задачу."),
    ],
)
async def test_failed_job_text_is_russian_and_keeps_job_type_and_error_out_of_chat(
    job_type: str, text: str, caplog: pytest.LogCaptureFixture
) -> None:
    """#89: the chat gets «Не удалось …» by job type, naming the Тема and Площадка where
    known; `job_type`, the Площадка key and the exception text only go to the log."""
    plan, article, gateway = FakePlan(), FakeArticle(), FakeGateway()
    handle = _make_notification_handler(plan, article, gateway, 42)

    with caplog.at_level(logging.WARNING):
        await handle(
            JobResult(job_id=5, job_type=job_type, status="failed", error="RuntimeError: boom")
        )

    assert gateway.sent_errors_with_retry == [(42, text, 5)]
    assert "RuntimeError: boom" in caplog.text


async def test_failed_article_job_marks_article_error_before_notifying() -> None:
    plan, article, gateway = FakePlan(), FakeArticle(), FakeGateway()
    handle = _make_notification_handler(plan, article, gateway, 42)

    await handle(JobResult(job_id=7, job_type="regenerate_article", status="failed", error="boom"))

    assert article.failed_jobs == [7]
    assert gateway.sent_errors_with_retry[0][2] == 7


async def test_stale_failed_article_job_is_ignored() -> None:
    plan, article, gateway = FakePlan(), FakeArticle(), FakeGateway()

    async def stale_failure(_source_job_id: int) -> None:
        return None

    article.mark_generation_failed = stale_failure  # type: ignore[method-assign]
    handle = _make_notification_handler(plan, article, gateway, 42)

    await handle(JobResult(job_id=7, job_type="generate_article", status="failed", error="old"))

    assert gateway.sent_errors_with_retry == []


async def test_generate_plan_appends_topics_and_sends_the_plan() -> None:
    plan, article, gateway = FakePlan(), FakeArticle(), FakeGateway()
    handle = _make_notification_handler(plan, article, gateway, 42)

    await handle(
        JobResult(
            job_id=1,
            job_type="generate_plan",
            status="done",
            output={
                "week_label": "Week 1",
                "topics": [{"title": "T1", "summary": "s", "keywords": ["k"]}],
            },
        )
    )

    assert plan.added_topics == [("Week 1", [TopicDraft(title="T1", summary="s", keywords=["k"])])]
    assert len(gateway.sent_plans) == 1
    assert gateway.sent_plans[0][0] == 42


async def test_redelivered_generate_plan_result_edits_the_canonical_message_instead_of_resending() -> (
    None
):
    """#73: a redelivered notification (e.g. a crash after the first Telegram send but before
    `notified_at` was marked) must not post a second Plan message - it should edit the one
    already recorded for this Plan."""
    plan, article, gateway = FakePlan(), FakeArticle(), FakeGateway()
    handle = _make_notification_handler(plan, article, gateway, 42)
    result = JobResult(
        job_id=1,
        job_type="generate_plan",
        status="done",
        output={
            "week_label": "Week 1",
            "topics": [{"title": "T1", "summary": "s", "keywords": ["k"]}],
        },
    )

    await handle(result)
    await handle(result)

    assert len(gateway.sent_plans) == 1
    assert len(gateway.edited_plans) == 1
    edited_chat_id, edited_message_id, _view = gateway.edited_plans[0]
    assert (edited_chat_id, edited_message_id) == (42, 1)


async def test_empty_generate_plan_result_creates_no_plan_and_explains_why() -> None:
    """#84: no Темы came out - no empty Plan (just an "Утвердить всё" that starts nothing),
    but a notice saying what happened and what to do."""
    plan, article, gateway = FakePlan(), FakeArticle(), FakeGateway()
    handle = _make_notification_handler(plan, article, gateway, 42)

    await handle(
        JobResult(
            job_id=1,
            job_type="generate_plan",
            status="done",
            output={
                "week_label": "2026-W41",
                "topics": [],
                "empty_reason": "no_growing_directions",
            },
        )
    )

    assert plan.added_topics == []
    assert gateway.sent_plans == [] and gateway.edited_plans == []
    ((chat_id, text),) = gateway.sent_notices
    assert chat_id == 42
    assert "5–11 октября 2026" in text and "не создан" in text
    assert "/topic" in text and "Направления" in text


async def test_empty_generate_plan_result_of_only_recent_repeats_says_so() -> None:
    plan, article, gateway = FakePlan(), FakeArticle(), FakeGateway()
    handle = _make_notification_handler(plan, article, gateway, 42)

    await handle(
        JobResult(
            job_id=1,
            job_type="generate_plan",
            status="done",
            output={"week_label": "2026-W41", "topics": [], "empty_reason": "all_recently_used"},
        )
    )

    assert "недавно уже были" in gateway.sent_notices[0][1]


async def test_regenerate_topic_applies_and_redraws_the_plan_message() -> None:
    """#81: the regenerated title appears in the Plan's canonical message, no separate notice."""
    plan, article, gateway = FakePlan(), FakeArticle(), FakeGateway()
    plan.message_refs[PlanId("plan-1")] = PlanMessageRef(chat_id=-100, message_id=5)
    handle = _make_notification_handler(plan, article, gateway, 42)

    await handle(
        JobResult(
            job_id=1,
            job_type="regenerate_topic",
            status="done",
            output={"plan_item_id": "item-1", "title": "New", "summary": "s", "keywords": ["k"]},
        )
    )

    assert plan.applied_regenerations == [
        ("item-1", TopicDraft(title="New", summary="s", keywords=["k"]))
    ]
    assert gateway.sent_notices == []
    assert [(c, m, view.id) for c, m, view in gateway.edited_plans] == [(-100, 5, "plan-1")]


async def test_article_research_status_is_recorded_on_the_version_not_in_its_text() -> None:
    plan, article, gateway = FakePlan(), FakeArticle(), FakeGateway()
    handle = _make_notification_handler(plan, article, gateway, 42)

    await handle(
        JobResult(
            job_id=1,
            job_type="generate_article",
            status="done",
            output={
                "article_id": "article-1",
                "content": "body",
                "prompt": "p",
                "model": "m",
                "tokens": 10,
                "cost": 0.0,
                "research_status": "no_evidence",
            },
        )
    )

    [(_, version)] = article.recorded_versions
    assert version.research_status == "no_evidence"
    assert version.content == "body"


async def test_stale_article_result_is_not_sent() -> None:
    plan, article, gateway = FakePlan(), FakeArticle(), FakeGateway()
    article.application = "stale"
    handle = _make_notification_handler(plan, article, gateway, 42)

    await handle(
        JobResult(
            job_id=1,
            job_type="regenerate_article",
            status="done",
            output={
                "article_id": "article-1",
                "content": "old",
                "prompt": "p",
                "model": "m",
                "tokens": 10,
                "cost": 0.0,
            },
        )
    )

    assert gateway.sent_articles == []


async def test_stale_failed_generate_cover_job_is_ignored() -> None:
    """Mirrors the existing stale-Article-failure behaviour: a job_id no plan_item currently
    owns (already superseded, e.g. by a later re-request) is dropped silently."""
    plan, article, gateway = FakePlan(), FakeArticle(), FakeGateway()
    plan.mark_cover_generation_failed_returns = None
    handle = _make_notification_handler(plan, article, gateway, 42)

    await handle(JobResult(job_id=3, job_type="generate_cover", status="failed", error="old"))

    assert gateway.sent_errors_with_retry == []
    assert gateway.hub_redraws == []


async def test_unknown_job_type_is_ignored() -> None:
    plan, article, gateway = FakePlan(), FakeArticle(), FakeGateway()
    handle = _make_notification_handler(plan, article, gateway, 42)

    await handle(JobResult(job_id=1, job_type="something_else", status="done", output={}))

    assert gateway.sent_errors == []
    assert gateway.sent_plans == []
    assert gateway.sent_articles == []


class FakePublisher:
    """Stands in for `telegraph.TelegraphPublisher` (#92): URL per article id, or `None`
    the way a real publish reports a Telegraph failure."""

    def __init__(self, urls: dict[str, str | None]) -> None:
        self._urls = urls
        self.published: list[str] = []

    async def publish(self, article: ArticleView) -> str | None:
        self.published.append(article.id)
        return self._urls.get(article.id)


def _article_result(job_type: str = "regenerate_article") -> JobResult:
    return JobResult(
        job_id=1,
        job_type=job_type,
        status="done",
        output={
            "article_id": "article-1",
            "content": "body",
            "prompt": "p",
            "model": "m",
            "tokens": 10,
            "cost": 0.0,
        },
    )


async def test_ready_article_card_carries_the_published_read_url() -> None:
    plan, article, gateway = FakePlan(), FakeArticle(), FakeGateway()
    publisher = FakePublisher({"article-1": "https://telegra.ph/T-10-07"})
    handle = _make_notification_handler(plan, article, gateway, 42, publisher=publisher)

    await handle(_article_result())

    assert publisher.published == ["article-1"]
    assert [a.id for _, a in gateway.sent_articles] == ["article-1"]
    assert gateway.article_read_urls == ["https://telegra.ph/T-10-07"]


async def test_telegraph_failure_still_delivers_the_card_without_read_url() -> None:
    plan, article, gateway = FakePlan(), FakeArticle(), FakeGateway()
    publisher = FakePublisher({"article-1": None})
    handle = _make_notification_handler(plan, article, gateway, 42, publisher=publisher)

    await handle(_article_result())

    assert [a.id for _, a in gateway.sent_articles] == ["article-1"]
    assert gateway.article_read_urls == [None]


async def test_generate_article_records_version_and_redraws_the_hub_without_a_card() -> None:
    """#91: a fan-out Статья shows up as ✅ in the Хаб (the Plan message) - no card of its own."""
    plan, article, gateway = FakePlan(), FakeArticle(), FakeGateway()
    plan.message_refs[PlanId("plan-1")] = PlanMessageRef(chat_id=-100, message_id=5)
    handle = _make_notification_handler(plan, article, gateway, 42)

    await handle(_article_result("generate_article"))

    assert article.recorded_versions == [
        (
            "article-1",
            GeneratedVersion(
                content="body", prompt="p", model="m", tokens=10, cost=0.0, source_job_id=1
            ),
        )
    ]
    assert gateway.sent_articles == []
    assert gateway.hub_redraws == [(-100, 5, "plan-1")]


async def test_generate_article_publishes_its_reading_page_for_the_hub() -> None:
    """The Хаб's result card links «📖» straight away (#91 + #92), so the page is published
    when the Статья is ready - without sending a card."""
    plan, article, gateway = FakePlan(), FakeArticle(), FakeGateway()
    publisher = FakePublisher({"article-1": "https://telegra.ph/a1"})
    handle = _make_notification_handler(plan, article, gateway, 42, publisher=publisher)

    await handle(_article_result("generate_article"))

    assert publisher.published == ["article-1"]
    assert gateway.sent_articles == []


async def test_redelivered_article_result_just_redraws_the_same_hub() -> None:
    """Progress is derived, not counted: a redelivered notification redraws the Хаб once more
    (Telegram's "not modified" is a success) and changes nothing else."""
    plan, article, gateway = FakePlan(), FakeArticle(), FakeGateway()
    plan.message_refs[PlanId("plan-1")] = PlanMessageRef(chat_id=-100, message_id=5)
    article.application = "already_applied"
    handle = _make_notification_handler(plan, article, gateway, 42)

    await handle(_article_result("generate_article"))
    await handle(_article_result("generate_article"))

    assert gateway.sent_articles == []
    assert gateway.hub_redraws == [(-100, 5, "plan-1")] * 2


async def test_hub_without_a_recorded_message_is_sent_once_then_edited() -> None:
    plan, article, gateway = FakePlan(), FakeArticle(), FakeGateway()
    handle = _make_notification_handler(plan, article, gateway, 42)

    await handle(_article_result("generate_article"))
    await handle(_article_result("generate_article"))

    assert gateway.hub_redraws == [(42, None, "plan-1"), (42, 1, "plan-1")]


async def test_result_for_an_archived_plan_redraws_nothing() -> None:
    plan, article, gateway = FakePlan(), FakeArticle(), FakeGateway()
    plan.plan_status = "archived"
    plan.message_refs[PlanId("plan-1")] = PlanMessageRef(chat_id=-100, message_id=5)
    handle = _make_notification_handler(plan, article, gateway, 42)

    await handle(_article_result("generate_article"))

    assert gateway.hub_redraws == []


async def test_regenerated_article_sends_its_card_and_redraws_the_hub() -> None:
    """«✏️ Доработать» was asked for by hand: its result is a card where the person is looking."""
    plan, article, gateway = FakePlan(), FakeArticle(), FakeGateway()
    plan.message_refs[PlanId("plan-1")] = PlanMessageRef(chat_id=-100, message_id=5)
    handle = _make_notification_handler(plan, article, gateway, 42)

    await handle(_article_result("regenerate_article"))

    assert [a.id for _, a in gateway.sent_articles] == ["article-1"]
    assert gateway.hub_redraws == [(-100, 5, "plan-1")]


async def test_failed_fan_out_article_only_turns_red_in_the_hub() -> None:
    """#91: no «Не удалось …» message per fan-out failure - the Хаб shows ❌ and carries
    «🔁 Повторить»."""
    plan, article, gateway = FakePlan(), FakeArticle(), FakeGateway()
    plan.message_refs[PlanId("plan-1")] = PlanMessageRef(chat_id=-100, message_id=5)
    handle = _make_notification_handler(plan, article, gateway, 42)

    await handle(JobResult(job_id=5, job_type="generate_article", status="failed", error="x"))

    assert article.failed_jobs == [5]
    assert gateway.sent_errors_with_retry == []
    assert gateway.hub_redraws == [(-100, 5, "plan-1")]


def _cover_result(job_id: int = 1) -> JobResult:
    image_b64 = base64.b64encode(b"image-bytes").decode("ascii")
    return JobResult(
        job_id=job_id,
        job_type="generate_cover",
        status="done",
        output={"plan_item_id": "item-1", "image": image_b64, "mime_type": "image/jpeg"},
    )


async def test_fan_out_cover_applies_and_redraws_the_hub_without_a_photo() -> None:
    plan, article, gateway = FakePlan(), FakeArticle(), FakeGateway()
    plan.message_refs[PlanId("plan-1")] = PlanMessageRef(chat_id=-100, message_id=5)
    handle = _make_notification_handler(plan, article, gateway, 42)

    await handle(_cover_result())

    assert plan.applied_covers == [("item-1", b"image-bytes", "image/jpeg")]
    assert gateway.sent_covers == []
    assert gateway.hub_redraws == [(-100, 5, "plan-1")]


async def test_manual_cover_is_sent_as_a_photo_and_redraws_the_hub() -> None:
    plan, article, gateway = FakePlan(), FakeArticle(), FakeGateway()
    plan.manual_cover_jobs = {1}
    plan.message_refs[PlanId("plan-1")] = PlanMessageRef(chat_id=-100, message_id=5)
    handle = _make_notification_handler(plan, article, gateway, 42)

    await handle(_cover_result())

    assert gateway.sent_covers == [(42, b"image-bytes", "image/jpeg", "Topic A")]
    assert gateway.hub_redraws == [(-100, 5, "plan-1")]


async def test_failed_fan_out_cover_only_turns_red_in_the_hub() -> None:
    plan, article, gateway = FakePlan(), FakeArticle(), FakeGateway()
    plan.message_refs[PlanId("plan-1")] = PlanMessageRef(chat_id=-100, message_id=5)
    handle = _make_notification_handler(plan, article, gateway, 42)

    await handle(JobResult(job_id=3, job_type="generate_cover", status="failed", error="boom"))

    assert plan.cover_failures_marked == [3]
    assert gateway.sent_errors_with_retry == []
    assert gateway.hub_redraws == [(-100, 5, "plan-1")]


async def test_failed_manual_cover_also_says_so_in_the_chat() -> None:
    plan, article, gateway = FakePlan(), FakeArticle(), FakeGateway()
    plan.manual_cover_jobs = {3}
    handle = _make_notification_handler(plan, article, gateway, 42)

    await handle(JobResult(job_id=3, job_type="generate_cover", status="failed", error="boom"))

    assert gateway.sent_errors_with_retry == [
        (42, "Не удалось сгенерировать обложку для Темы «Topic A»", 3)
    ]


# --- #114: the team chat's pinned note ---


class FakeTeamNote:
    def __init__(self) -> None:
        self.ensured = 0

    async def ensure(self) -> None:
        self.ensured += 1


_PLAN_RESULT = JobResult(
    job_id=1,
    job_type="generate_plan",
    status="done",
    output={"week_label": "Week 1", "topics": [{"title": "T1", "summary": "s", "keywords": []}]},
)


async def test_a_plan_delivery_is_preceded_by_the_team_note() -> None:
    plan, article, gateway = FakePlan(), FakeArticle(), FakeGateway()
    note = FakeTeamNote()
    handle = _make_notification_handler(plan, article, gateway, 42, team_note=note)

    await handle(_PLAN_RESULT)
    await handle(_PLAN_RESULT)

    assert note.ensured == 2  # TeamNote itself posts only once


async def test_nothing_for_the_chat_means_no_team_note() -> None:
    plan, article, gateway = FakePlan(), FakeArticle(), FakeGateway()
    note = FakeTeamNote()
    handle = _make_notification_handler(plan, article, gateway, 42, team_note=note)

    await handle(JobResult(job_id=9, job_type="some_future_job", status="done", output={}))

    assert note.ensured == 0


class FakeDirections:
    def __init__(self) -> None:
        self.delivered: list[JobResult] = []

    async def deliver(self, result: JobResult) -> None:
        self.delivered.append(result)


@pytest.mark.parametrize("status", ["done", "failed"])
async def test_directions_suggestion_goes_to_its_own_message_not_the_team_chat(
    status: str,
) -> None:
    """#113: a suggestion (or its failure) edits the Владелец's «⏳» message; nothing is
    written to the domain and nothing lands in the team chat."""
    plan, article, gateway = FakePlan(), FakeArticle(), FakeGateway()
    directions = FakeDirections()
    handle = _make_notification_handler(plan, article, gateway, 42, directions=directions)
    result = JobResult(
        job_id=9, job_type="suggest_directions", status=status, output={"queries": []}
    )

    await handle(result)

    assert directions.delivered == [result]
    assert gateway.sent_errors_with_retry == [] and gateway.sent_notices == []
