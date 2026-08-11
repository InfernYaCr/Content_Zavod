"""generate_plan Job Handler: sources a week's Тем from real Wordstat growth.

Per ADR-0006's 2026-08-07 amendment, "growing" is determined from
`KeywordStats.keyword_dynamics()` month-over-month counts for a fixed seed
list of Niche keywords, not a static high-frequency-now snapshot. The seed
list stays a module constant for now (see ADR-0006) - only the free-text
Niche description embedded in the prompts is Owner-editable (#36), read
fresh from `OwnerSettingsStore` on every call so a `/set_niche` takes effect
without a restart; dedup against topic history is delegated to the
caller-supplied `recent_topic_titles`.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Protocol, Sequence

from ..domain import PlanItemDetail, PlanItemId, TopicDraft
from ..job_queue import JobHandler
from ..yandex import KeywordDynamicsPoint, KeywordStats, Message, TextGenerator

NICHE_KEY = "niche"
DEFAULT_NICHE = "маркетинг"

NICHE_SEED_KEYWORDS: Sequence[str] = (
    "crm для малого бизнеса",
    "email маркетинг",
    "таргетированная реклама",
    "контент маркетинг",
    "seo продвижение сайта",
    "воронка продаж",
    "юнит экономика",
    "маркетинговая стратегия",
)

TOPICS_PER_PLAN = 3
DYNAMICS_MONTHS = 6
RECENT_HISTORY_DAYS = 90


class OwnerSettingsOperations(Protocol):
    async def get(self, key: str) -> str | None: ...


async def _current_niche(owner_settings: OwnerSettingsOperations) -> str:
    value = await owner_settings.get(NICHE_KEY)
    return value if value else DEFAULT_NICHE


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
    owner_settings: OwnerSettingsOperations,
    *,
    seed_keywords: Sequence[str] = NICHE_SEED_KEYWORDS,
    topics_per_plan: int = TOPICS_PER_PLAN,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> JobHandler:
    async def handle(payload: dict[str, Any]) -> dict[str, Any]:
        week_label = payload["week_label"]
        current_time = now()
        from_date, to_date = _dynamics_window(current_time)
        niche = await _current_niche(owner_settings)

        growing: list[tuple[float, str]] = []
        for keyword in seed_keywords:
            try:
                points = await keyword_stats.keyword_dynamics(
                    keyword, period="PERIOD_MONTHLY", from_date=from_date, to_date=to_date
                )
            except Exception:  # noqa: BLE001 - one bad keyword must not sink the whole Plan
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
    owner_settings: OwnerSettingsOperations,
) -> JobHandler:
    async def handle(payload: dict[str, Any]) -> dict[str, Any]:
        plan_item_id = PlanItemId(payload["plan_item_id"])
        comment = payload.get("comment")
        current = await item_reader.get_item(plan_item_id)
        niche = await _current_niche(owner_settings)
        draft = await _redraft_topic(text_generator, current, comment, niche)
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
