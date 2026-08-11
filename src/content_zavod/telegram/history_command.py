"""handle_history_command / handle_history_page / handle_history_week: the read-only
/history browser (#29).

Available to both roles. Lists every Plan regardless of status - including the
current unfinished `pending_review` week - newest first, paginated the same
way a Plan's item list already is. Selecting a week edits the same message
into that week's Статьи, each with its status shown as plain text; nothing is
hidden or filtered. No download button yet (a follow-up ticket).
"""

from __future__ import annotations

from typing import Protocol

from ..domain import ArticleSummary, PlanId, PlanSummary
from .gateway import ITEMS_PER_PAGE, TelegramGateway, decode_history_week_id, total_pages


class HistoryPlans(Protocol):
    async def list_page(self, *, page: int, page_size: int) -> tuple[list[PlanSummary], int]: ...

    async def get_summary(self, plan_id: PlanId) -> PlanSummary: ...


class HistoryArticles(Protocol):
    async def list_summary_for_plan(self, plan_id: PlanId) -> list[ArticleSummary]: ...


async def handle_history_command(plans: HistoryPlans, gateway: TelegramGateway, chat_id: int) -> None:
    plans_page, total = await plans.list_page(page=0, page_size=ITEMS_PER_PAGE)
    await gateway.send_history_weeks(chat_id, plans_page, page=0, page_count=total_pages(total))


async def handle_history_page(
    plans: HistoryPlans, gateway: TelegramGateway, chat_id: int, message_id: int, page: int
) -> None:
    plans_page, total = await plans.list_page(page=page, page_size=ITEMS_PER_PAGE)
    await gateway.edit_history_weeks(chat_id, message_id, plans_page, page=page, page_count=total_pages(total))


async def handle_history_week(
    plans: HistoryPlans,
    articles: HistoryArticles,
    gateway: TelegramGateway,
    chat_id: int,
    message_id: int,
    id_: str,
) -> None:
    plan_id_text, back_page = decode_history_week_id(id_)
    plan_id = PlanId(plan_id_text)
    summary = await plans.get_summary(plan_id)
    article_summaries = await articles.list_summary_for_plan(plan_id)
    await gateway.edit_history_articles(chat_id, message_id, summary, article_summaries, back_page=back_page)
