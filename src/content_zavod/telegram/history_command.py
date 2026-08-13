"""handle_history_command / handle_history_page / handle_history_week / handle_history_versions /
handle_history_version: the read-only /history browser (#29).

Available to both roles. Lists every Plan regardless of status - including the
current unfinished `pending_review` week - newest first, paginated the same
way a Plan's item list already is. Selecting a week edits the same message
into that week's Статьи, each with its status shown as plain text; nothing is
hidden or filtered. Each Статья with a downloadable last Версия gets a
"скачать" row (.docx/.md), reusing the `export_article` callback/keyboard
machinery from #28 (#30), plus a "Версии" row into its full version history -
metadata list, then a chosen Версия's content (#26). Only the latest Версия
is exportable; older ones are view-only.
"""

from __future__ import annotations

from typing import Protocol

from ..domain import (
    ArticleId,
    ArticleSummary,
    ArticleVersionSummary,
    ArticleVersionView,
    PlanId,
    PlanSummary,
)
from .callback_codec import HistoryVersion, HistoryVersions, HistoryWeek
from .gateway import ITEMS_PER_PAGE, TelegramGateway, total_pages


class HistoryPlans(Protocol):
    async def list_page(self, *, page: int, page_size: int) -> tuple[list[PlanSummary], int]: ...

    async def get_summary(self, plan_id: PlanId) -> PlanSummary: ...


class HistoryArticles(Protocol):
    async def list_summary_for_plan(self, plan_id: PlanId) -> list[ArticleSummary]: ...

    async def get_summary(self, article_id: ArticleId) -> ArticleSummary: ...

    async def get_plan_id(self, article_id: ArticleId) -> PlanId: ...

    async def list_versions(self, article_id: ArticleId) -> list[ArticleVersionSummary]: ...

    async def get_version(self, article_id: ArticleId, version_id: int) -> ArticleVersionView: ...


async def handle_history_command(
    plans: HistoryPlans, gateway: TelegramGateway, chat_id: int
) -> None:
    plans_page, total = await plans.list_page(page=0, page_size=ITEMS_PER_PAGE)
    await gateway.send_history_weeks(chat_id, plans_page, page=0, page_count=total_pages(total))


async def handle_history_page(
    plans: HistoryPlans, gateway: TelegramGateway, chat_id: int, message_id: int, page: int
) -> None:
    plans_page, total = await plans.list_page(page=page, page_size=ITEMS_PER_PAGE)
    await gateway.edit_history_weeks(
        chat_id, message_id, plans_page, page=page, page_count=total_pages(total)
    )


async def handle_history_week(
    plans: HistoryPlans,
    articles: HistoryArticles,
    gateway: TelegramGateway,
    chat_id: int,
    message_id: int,
    payload: HistoryWeek,
) -> None:
    plan_id = PlanId(payload.plan_id)
    summary = await plans.get_summary(plan_id)
    article_summaries = await articles.list_summary_for_plan(plan_id)
    await gateway.edit_history_articles(
        chat_id, message_id, summary, article_summaries, back_page=payload.page
    )


async def handle_history_versions(
    articles: HistoryArticles,
    gateway: TelegramGateway,
    chat_id: int,
    message_id: int,
    payload: HistoryVersions,
) -> None:
    article_id = ArticleId(payload.article_id)
    article_summary = await articles.get_summary(article_id)
    plan_id = await articles.get_plan_id(article_id)
    versions = await articles.list_versions(article_id)
    await gateway.edit_history_versions(
        chat_id, message_id, article_summary, plan_id, versions, back_page=payload.back_page
    )


async def handle_history_version(
    articles: HistoryArticles,
    gateway: TelegramGateway,
    chat_id: int,
    message_id: int,
    payload: HistoryVersion,
) -> None:
    article_id = ArticleId(payload.article_id)
    article_summary = await articles.get_summary(article_id)
    version = await articles.get_version(article_id, payload.version_id)
    await gateway.edit_history_version(
        chat_id, message_id, article_summary, version, back_page=payload.back_page
    )
