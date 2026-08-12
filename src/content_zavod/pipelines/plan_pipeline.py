"""generate_plan Job Handler: sources a week's Тем from real Wordstat growth.

Per ADR-0006's 2026-08-07 amendment, "growing" is determined from
`KeywordStats.keyword_dynamics()` month-over-month counts for a seed list of
Направления (Wordstat seed keywords), not a static high-frequency-now
snapshot. Ниша and Направления live in the `settings` module (#49), which
this pipeline reads fresh from a `SettingsReader` at the start of every Job
run so a change takes effect without a restart - the constants and
`parse_directions` below are aliases into that module, kept here so `/settings`
and the other existing importers don't need to change. `seed_keywords`
remains an explicit override for callers (mainly tests) that want to bypass
Настройки entirely; dedup against topic history is delegated to the
caller-supplied `recent_topic_titles`.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from ..domain import PlanItemDetail, PlanItemId, TopicDraft
from ..job_queue import JobHandler
from ..settings import (
    DEFAULT_DIRECTIONS,
    DEFAULT_NICHE,
    DIRECTIONS_KEY,
    NICHE_KEY,
    SettingsReader,
    parse_directions,
)
from ..yandex import KeywordDynamicsPoint, KeywordStats, Message, TextGenerator

__all__ = [
    "DEFAULT_DIRECTIONS",
    "DEFAULT_NICHE",
    "DIRECTIONS_KEY",
    "NICHE_KEY",
    "make_generate_plan_handler",
    "make_regenerate_topic_handler",
    "parse_directions",
]

TOPICS_PER_PLAN = 3
DYNAMICS_MONTHS = 6
RECENT_HISTORY_DAYS = 90


def _topic_prompt_system(niche: str) -> str:
    return (
        f"Ты - контент-стратег в Нише «{niche}». По одному растущему поисковому "
        "запросу предложи одну Тему для контент-плана. Ответь строго в формате:\n"
        "Title: <заголовок>\n"
        "Summary: <краткое описание в 1-2 предложения>\n"
        "Keywords: <ключевые слова через запятую>"
    )


def make_generate_plan_handler(
    keyword_stats: KeywordStats,
    text_generator: TextGenerator,
    recent_topic_titles: Callable[[datetime], Awaitable[list[str]]],
    settings: SettingsReader,
    *,
    seed_keywords: Sequence[str] | None = None,
    topics_per_plan: int = TOPICS_PER_PLAN,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> JobHandler:
    async def handle(payload: dict[str, Any]) -> dict[str, Any]:
        week_label = payload["week_label"]
        current_time = now()
        from_date, to_date = _dynamics_window(current_time)
        owner_settings = await settings.read()
        niche = owner_settings.niche
        directions = seed_keywords if seed_keywords is not None else owner_settings.directions

        growing: list[tuple[float, str]] = []
        for keyword in directions:
            try:
                points = await keyword_stats.keyword_dynamics(
                    keyword, period="PERIOD_MONTHLY", from_date=from_date, to_date=to_date
                )
            except Exception:
                continue
            growth = _growth_ratio(points)
            if growth is not None and growth > 1.0:
                growing.append((growth, keyword))
        growing.sort(key=lambda pair: pair[0], reverse=True)

        since = current_time - timedelta(days=RECENT_HISTORY_DAYS)
        used_titles = {title.lower() for title in await recent_topic_titles(since)}

        topics: list[dict[str, Any]] = []
        for _, keyword in growing:
            if len(topics) >= topics_per_plan:
                break
            draft = await _draft_topic(text_generator, keyword, niche)
            if draft.title.lower() in used_titles:
                continue
            topics.append(
                {"title": draft.title, "summary": draft.summary, "keywords": list(draft.keywords)}
            )
            used_titles.add(draft.title.lower())

        return {"week_label": week_label, "topics": topics}

    return handle


class PlanItemReader(Protocol):
    async def get_item(self, plan_item_id: PlanItemId) -> PlanItemDetail: ...


def _regenerate_prompt_system(niche: str) -> str:
    return (
        f"Ты - контент-стратег в Нише «{niche}». Тебе дали существующую Тему для "
        "контент-плана и комментарий, что в ней поправить. Предложи обновлённый "
        "вариант этой же Темы. Ответь строго в формате:\n"
        "Title: <заголовок>\n"
        "Summary: <краткое описание в 1-2 предложения>\n"
        "Keywords: <ключевые слова через запятую>"
    )


def make_regenerate_topic_handler(
    item_reader: PlanItemReader,
    text_generator: TextGenerator,
    settings: SettingsReader,
) -> JobHandler:
    async def handle(payload: dict[str, Any]) -> dict[str, Any]:
        plan_item_id = PlanItemId(payload["plan_item_id"])
        comment = payload.get("comment")
        current = await item_reader.get_item(plan_item_id)
        owner_settings = await settings.read()
        draft = await _redraft_topic(text_generator, current, comment, owner_settings.niche)
        return {
            "plan_item_id": plan_item_id,
            "title": draft.title,
            "summary": draft.summary,
            "keywords": list(draft.keywords),
        }

    return handle


async def _redraft_topic(
    text_generator: TextGenerator, current: PlanItemDetail, comment: str | None, niche: str
) -> TopicDraft:
    user_text = (
        f"Текущая Тема:\nTitle: {current.title}\nSummary: {current.summary}\n"
        f"Keywords: {', '.join(current.keywords)}\n\n"
        f"Комментарий: {comment or '(без комментария)'}"
    )
    text = await text_generator.complete(
        [
            Message(role="system", text=_regenerate_prompt_system(niche)),
            Message(role="user", text=user_text),
        ]
    )
    return _parse_topic(text, fallback_keyword=current.title)


def _dynamics_window(now: datetime) -> tuple[str, str]:
    to_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    from_month = to_month
    for _ in range(DYNAMICS_MONTHS):
        from_month = (from_month - timedelta(days=1)).replace(day=1)
    return _to_api_date(from_month), _to_api_date(to_month)


def _to_api_date(value: datetime) -> str:
    return value.strftime("%Y-%m-%dT00:00:00Z")


def _growth_ratio(points: list[KeywordDynamicsPoint]) -> float | None:
    if len(points) < 2:
        return None
    first, last = points[0].count, points[-1].count
    if first <= 0:
        return None
    return last / first


async def _draft_topic(text_generator: TextGenerator, keyword: str, niche: str) -> TopicDraft:
    text = await text_generator.complete(
        [
            Message(role="system", text=_topic_prompt_system(niche)),
            Message(role="user", text=f"Растущий поисковый запрос: «{keyword}». Предложи Тему."),
        ]
    )
    return _parse_topic(text, fallback_keyword=keyword)


def _parse_topic(text: str, *, fallback_keyword: str) -> TopicDraft:
    fields = {"title": "", "summary": "", "keywords": ""}
    for line in text.splitlines():
        stripped = line.strip()
        for name in fields:
            prefix = f"{name}:"
            if stripped.lower().startswith(prefix):
                fields[name] = stripped[len(prefix) :].strip()
    title = fields["title"] or fallback_keyword
    keywords = [k.strip() for k in fields["keywords"].split(",") if k.strip()] or [fallback_keyword]
    return TopicDraft(title=title, summary=fields["summary"], keywords=keywords)
