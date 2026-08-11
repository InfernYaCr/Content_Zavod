from __future__ import annotations

from datetime import datetime, timezone

import pytest

from content_zavod.domain import (
    ArticleId,
    ArticleSummary,
    ArticleVersionSummary,
    ArticleVersionView,
    PlanId,
    PlanSummary,
)
from content_zavod.telegram.gateway import ITEMS_PER_PAGE, TelegramGateway, decode_callback_data
from content_zavod.telegram.history_command import (
    handle_history_command,
    handle_history_page,
    handle_history_version,
    handle_history_versions,
    handle_history_week,
)


class FakeBot:
    def __init__(self) -> None:
        self.sent_messages: list[tuple[int, str, object]] = []
        self.edited_messages: list[tuple[int, int, str, object]] = []

    async def send_message(self, chat_id, text, reply_markup=None) -> int:
        self.sent_messages.append((chat_id, text, reply_markup))
        return len(self.sent_messages)

    async def edit_message_text(self, chat_id, message_id, text, reply_markup=None) -> None:
        self.edited_messages.append((chat_id, message_id, text, reply_markup))


class FakePlans:
    def __init__(self, plans: list[PlanSummary]) -> None:
        self._plans = plans

    async def list_page(self, *, page: int, page_size: int) -> tuple[list[PlanSummary], int]:
        start = page * page_size
        return self._plans[start : start + page_size], len(self._plans)

    async def get_summary(self, plan_id: PlanId) -> PlanSummary:
        for item in self._plans:
            if item.id == plan_id:
                return item
        raise AssertionError(f"no such plan {plan_id!r}")


class FakeArticles:
    def __init__(
        self,
        by_plan: dict[str, list[ArticleSummary]],
        *,
        plan_id_by_article: dict[str, str] | None = None,
        versions_by_article: dict[str, list[ArticleVersionSummary]] | None = None,
        version_content: dict[tuple[str, int], str] | None = None,
    ) -> None:
        self._by_plan = by_plan
        self._plan_id_by_article = plan_id_by_article or {}
        self._versions_by_article = versions_by_article or {}
        self._version_content = version_content or {}

    async def list_summary_for_plan(self, plan_id: PlanId) -> list[ArticleSummary]:
        return self._by_plan.get(plan_id, [])

    async def get_summary(self, article_id: ArticleId) -> ArticleSummary:
        for articles in self._by_plan.values():
            for item in articles:
                if item.id == article_id:
                    return item
        raise AssertionError(f"no such article {article_id!r}")

    async def get_plan_id(self, article_id: ArticleId) -> PlanId:
        return PlanId(self._plan_id_by_article[article_id])

    async def list_versions(self, article_id: ArticleId) -> list[ArticleVersionSummary]:
        return self._versions_by_article.get(article_id, [])

    async def get_version(self, article_id: ArticleId, version_id: int) -> ArticleVersionView:
        content = self._version_content[(article_id, version_id)]
        (summary,) = [v for v in self._versions_by_article[article_id] if v.id == version_id]
        return ArticleVersionView(
            id=summary.id,
            content=content,
            model=summary.model,
            tokens=summary.tokens,
            cost=summary.cost,
            created_at=summary.created_at,
        )


def _plan(n: int, status: str = "pending_review") -> PlanSummary:
    return PlanSummary(id=PlanId(f"plan-{n}"), week_label="2026-W32", status=status)


@pytest.mark.asyncio
async def test_history_command_sends_first_page_of_weeks() -> None:
    plans = FakePlans([_plan(1)])
    gateway = TelegramGateway(FakeBot())

    await handle_history_command(plans, gateway, chat_id=1)

    assert len(gateway._bot.sent_messages) == 1
    chat_id, text, keyboard = gateway._bot.sent_messages[0]
    assert chat_id == 1
    assert "pending_review" in text
    assert len(keyboard.inline_keyboard) == 1


@pytest.mark.asyncio
async def test_history_command_includes_current_unfinished_week_like_any_other() -> None:
    plans = FakePlans([_plan(1, status="pending_review"), _plan(2, status="archived")])
    gateway = TelegramGateway(FakeBot())

    await handle_history_command(plans, gateway, chat_id=1)

    _, text, keyboard = gateway._bot.sent_messages[0]
    assert "pending_review" in text and "archived" in text
    assert len(keyboard.inline_keyboard) == 2


@pytest.mark.asyncio
async def test_history_command_empty_still_sends_a_message_not_a_notice() -> None:
    plans = FakePlans([])
    gateway = TelegramGateway(FakeBot())

    await handle_history_command(plans, gateway, chat_id=1)

    _, text, keyboard = gateway._bot.sent_messages[0]
    assert "Планов пока нет" in text
    assert keyboard.inline_keyboard == []


@pytest.mark.asyncio
async def test_history_page_edits_the_message_with_the_requested_page() -> None:
    all_plans = [_plan(i) for i in range(ITEMS_PER_PAGE + 1)]
    plans = FakePlans(all_plans)
    gateway = TelegramGateway(FakeBot())

    await handle_history_page(plans, gateway, chat_id=1, message_id=5, page=1)

    chat_id, message_id, text, keyboard = gateway._bot.edited_messages[0]
    assert (chat_id, message_id) == (1, 5)
    assert "Страница 2/2" in text
    # one leftover week's row, plus a nav row with only "Назад" (no next page)
    assert len(keyboard.inline_keyboard) == 2
    assert len(keyboard.inline_keyboard[1]) == 1


@pytest.mark.asyncio
async def test_history_week_edits_the_message_with_that_weeks_articles() -> None:
    plan_summary = _plan(1, status="approved")
    plans = FakePlans([plan_summary])
    articles = FakeArticles(
        {
            "plan-1": [
                ArticleSummary(id=ArticleId("a-1"), title="Topic A", platform="zen", status="queued"),
                ArticleSummary(id=ArticleId("a-2"), title="Topic A", platform="vc", status="generating"),
            ]
        }
    )
    gateway = TelegramGateway(FakeBot())

    await handle_history_week(plans, articles, gateway, chat_id=1, message_id=5, id_="plan-1:0")

    chat_id, message_id, text, keyboard = gateway._bot.edited_messages[0]
    assert (chat_id, message_id) == (1, 5)
    assert "Topic A (zen) — queued" in text
    assert "Topic A (vc) — generating" in text
    # A single "Назад" button, returning to the week list's page 0.
    assert len(keyboard.inline_keyboard) == 1
    back_button = keyboard.inline_keyboard[0][0]
    action, id_ = decode_callback_data(back_button.callback_data)
    assert (action, id_) == ("history_page", "0")


@pytest.mark.asyncio
async def test_history_week_with_no_articles_yet() -> None:
    plans = FakePlans([_plan(1)])
    articles = FakeArticles({})
    gateway = TelegramGateway(FakeBot())

    await handle_history_week(plans, articles, gateway, chat_id=1, message_id=5, id_="plan-1:0")

    _, _, text, _ = gateway._bot.edited_messages[0]
    assert "Статей пока нет" in text


def _version(id_: int, model: str = "yandexgpt") -> ArticleVersionSummary:
    return ArticleVersionSummary(
        id=id_, model=model, tokens=42, cost=0.01, created_at=datetime(2026, 8, 11, 14, 3, tzinfo=timezone.utc)
    )


@pytest.mark.asyncio
async def test_history_versions_edits_the_message_with_that_articles_versions() -> None:
    article_summary = ArticleSummary(id=ArticleId("a-1"), title="Topic A", platform="zen", status="ready")
    articles = FakeArticles(
        {"plan-1": [article_summary]},
        plan_id_by_article={"a-1": "plan-1"},
        versions_by_article={"a-1": [_version(2, "yandexgpt-2"), _version(1)]},
    )
    gateway = TelegramGateway(FakeBot())

    await handle_history_versions(articles, gateway, chat_id=1, message_id=5, id_="a-1:0")

    chat_id, message_id, text, keyboard = gateway._bot.edited_messages[0]
    assert (chat_id, message_id) == (1, 5)
    assert "Topic A (zen)" in text
    action, id_ = decode_callback_data(keyboard.inline_keyboard[-1][0].callback_data)
    assert (action, id_) == ("history_week", "plan-1:0")


@pytest.mark.asyncio
async def test_history_versions_with_no_versions_yet() -> None:
    article_summary = ArticleSummary(id=ArticleId("a-1"), title="Topic A", platform="zen", status="queued")
    articles = FakeArticles({"plan-1": [article_summary]}, plan_id_by_article={"a-1": "plan-1"})
    gateway = TelegramGateway(FakeBot())

    await handle_history_versions(articles, gateway, chat_id=1, message_id=5, id_="a-1:0")

    _, _, text, _ = gateway._bot.edited_messages[0]
    assert "Версий пока нет" in text


@pytest.mark.asyncio
async def test_history_version_edits_the_message_with_that_versions_content() -> None:
    article_summary = ArticleSummary(id=ArticleId("a-1"), title="Topic A", platform="zen", status="ready")
    articles = FakeArticles(
        {"plan-1": [article_summary]},
        plan_id_by_article={"a-1": "plan-1"},
        versions_by_article={"a-1": [_version(2)]},
        version_content={("a-1", 2): "Hello, world."},
    )
    gateway = TelegramGateway(FakeBot())

    await handle_history_version(articles, gateway, chat_id=1, message_id=5, id_="a-1:2:0")

    chat_id, message_id, text, keyboard = gateway._bot.edited_messages[0]
    assert (chat_id, message_id) == (1, 5)
    assert text.endswith("Hello, world.")
    (back_button,) = keyboard.inline_keyboard[0]
    assert decode_callback_data(back_button.callback_data) == ("history_versions", "a-1:0")
