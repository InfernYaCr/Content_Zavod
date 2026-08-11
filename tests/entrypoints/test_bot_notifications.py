"""Unit tests for bot_main's notification-dispatch glue (`_make_notification_handler`).

Everything else in entrypoints/bot.py is aiogram/Postgres wiring, verified by
local manual runs per issue #13's acceptance criteria - this is the one piece
of non-trivial logic (routing a JobResult to the right domain call + gateway
render) worth a fast unit test.
"""

from __future__ import annotations

from content_zavod.domain import ArticleView, GeneratedVersion, PlanId, PlanItemId, PlanView, TopicDraft
from content_zavod.domain.plan import PlanItemDetail
from content_zavod.entrypoints.bot import _make_notification_handler
from content_zavod.job_queue import JobResult


class FakePlan:
    def __init__(self) -> None:
        self.added_topics: list[tuple[str, list[TopicDraft]]] = []
        self.applied_regenerations: list[tuple[str, TopicDraft]] = []
        self.applied_covers: list[tuple[str, bytes, str]] = []

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


class FakeArticle:
    def __init__(self) -> None:
        self.recorded_versions: list[tuple[str, GeneratedVersion]] = []

    async def record_version(self, article_id: str, version: GeneratedVersion) -> None:
        self.recorded_versions.append((article_id, version))

    async def get(self, article_id: str) -> ArticleView:
        return ArticleView(
            id=article_id, plan_item_id="item-1", title="T", platform="P", filename="f.txt", content=b"c"
        )


class FakeGateway:
    def __init__(self) -> None:
        self.sent_plans: list[tuple[int, PlanView]] = []
        self.sent_articles: list[tuple[int, ArticleView]] = []
        self.sent_errors: list[tuple[int, str]] = []
        self.sent_errors_with_retry: list[tuple[int, str, int]] = []
        self.sent_notices: list[tuple[int, str]] = []
        self.sent_covers: list[tuple[int, bytes, str, str]] = []

    async def send_plan(self, chat_id: int, plan: PlanView) -> None:
        self.sent_plans.append((chat_id, plan))

    async def send_article_ready(self, chat_id: int, article: ArticleView) -> None:
        self.sent_articles.append((chat_id, article))

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

    assert gateway.sent_errors_with_retry == [
        (42, "Задача generate_plan завершилась ошибкой: boom", 1)
    ]


async def test_generate_plan_appends_topics_and_sends_the_plan() -> None:
    plan, article, gateway = FakePlan(), FakeArticle(), FakeGateway()
    handle = _make_notification_handler(plan, article, gateway, 42)

    await handle(
        JobResult(
            job_id=1,
            job_type="generate_plan",
            status="done",
            output={"week_label": "Week 1", "topics": [{"title": "T1", "summary": "s", "keywords": ["k"]}]},
        )
    )

    assert plan.added_topics == [("Week 1", [TopicDraft(title="T1", summary="s", keywords=["k"])])]
    assert len(gateway.sent_plans) == 1
    assert gateway.sent_plans[0][0] == 42


async def test_regenerate_topic_applies_and_notifies() -> None:
    plan, article, gateway = FakePlan(), FakeArticle(), FakeGateway()
    handle = _make_notification_handler(plan, article, gateway, 42)

    await handle(
        JobResult(
            job_id=1,
            job_type="regenerate_topic",
            status="done",
            output={"plan_item_id": "item-1", "title": "New", "summary": "s", "keywords": ["k"]},
        )
    )

    assert plan.applied_regenerations == [("item-1", TopicDraft(title="New", summary="s", keywords=["k"]))]
    assert gateway.sent_notices == [(42, "Тема обновлена: New")]


async def test_generate_article_records_version_and_sends_article() -> None:
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
            },
        )
    )

    assert article.recorded_versions == [
        ("article-1", GeneratedVersion(content="body", prompt="p", model="m", tokens=10, cost=0.0))
    ]
    assert len(gateway.sent_articles) == 1


async def test_generate_cover_applies_cover_and_notifies() -> None:
    import base64

    plan, article, gateway = FakePlan(), FakeArticle(), FakeGateway()
    handle = _make_notification_handler(plan, article, gateway, 42)
    image_b64 = base64.b64encode(b"image-bytes").decode("ascii")

    await handle(
        JobResult(
            job_id=1,
            job_type="generate_cover",
            status="done",
            output={"plan_item_id": "item-1", "image": image_b64, "mime_type": "image/jpeg"},
        )
    )

    assert plan.applied_covers == [("item-1", b"image-bytes", "image/jpeg")]
    assert gateway.sent_covers == [(42, b"image-bytes", "image/jpeg", "Topic A")]


async def test_unknown_job_type_is_ignored() -> None:
    plan, article, gateway = FakePlan(), FakeArticle(), FakeGateway()
    handle = _make_notification_handler(plan, article, gateway, 42)

    await handle(JobResult(job_id=1, job_type="something_else", status="done", output={}))

    assert gateway.sent_errors == []
    assert gateway.sent_plans == []
    assert gateway.sent_articles == []
