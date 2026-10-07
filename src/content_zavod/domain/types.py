"""Shared value types for the domain layer: identifiers and view DTOs.

`PlanView`/`PlanItemView`/`ArticleView` are the consumer contract the
Telegram layer (#4) was built against — keep their shape stable.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, NewType

PlanId = NewType("PlanId", str)
PlanItemId = NewType("PlanItemId", str)
ArticleId = NewType("ArticleId", str)

PlanStatus = Literal["pending_review", "approved", "archived"]
PlanItemStatus = Literal["pending_review", "approved", "rejected", "archived"]
ArticleStatus = Literal["queued", "generating", "error", "ready", "regenerating", "exported"]
ArticleFormat = Literal["docx", "md"]

# MVP Площадки (see CONTEXT.md) - one Статья per Площадка is fanned out per approved Тема (#14).
PLATFORMS: tuple[str, ...] = ("zen", "vc")


@dataclass(frozen=True)
class PlanItemView:
    id: PlanItemId
    title: str
    status: str


@dataclass(frozen=True)
class PlanView:
    id: PlanId
    week_label: str
    items: Sequence[PlanItemView]


@dataclass(frozen=True)
class ArticleView:
    id: ArticleId
    plan_item_id: PlanItemId
    title: str
    platform: str
    content: bytes
    # The latest Версия's Исследование status (#94): `no_evidence`/`search_unavailable`
    # mean the text was written without verified facts - the Article card warns the editor.
    # `None` for Версии generated before #94.
    research_status: str | None = None


@dataclass(frozen=True)
class PlanSummary:
    """A Plan's header only, for /history's week list - no items join."""

    id: PlanId
    week_label: str
    status: str


@dataclass(frozen=True)
class ArticleSummary:
    """An Article's header only, for /history's article list - no content lookup,
    so a not-yet-generated Статья (`queued`/`generating`/`error`) still shows up."""

    id: ArticleId
    title: str
    platform: str
    status: str


@dataclass(frozen=True)
class PlanMessageRef:
    """The canonical Telegram message a Plan is rendered as (ADR-0005, #73):
    at most one per Plan, recorded once the first delivery sends it so a
    later delivery for the same Plan edits it instead of sending another."""

    chat_id: int
    message_id: int


@dataclass(frozen=True)
class PlanItemCoverView:
    """One Тема's generated cover image, read back out of the DB when the Хаб's result card
    asks for it (#91) - the Хаб itself is a text message, so the cover is sent on demand."""

    plan_item_id: PlanItemId
    title: str
    image: bytes
    mime_type: str


# A Хаб cell's state (#91), derived from statuses on every render - never stored or counted.
HubCellState = Literal["pending", "ready", "failed"]


@dataclass(frozen=True)
class HubArticleCell:
    """One Площадка's Статья for a Тема in the Хаб. `article_id` is `None` only in the crash
    window between approving and the fan-out creating the row (shown as ⏳). `has_content`
    says whether a Версия exists to open - an `error` after a successful earlier Версия still
    has one. `job_id` is the Job currently owning its generation, what «🔁 Повторить» retries.
    `telegraph_path` is its Страница для чтения (#92), if published."""

    platform: str
    state: HubCellState
    article_id: ArticleId | None = None
    has_content: bool = False
    job_id: int | None = None
    telegraph_path: str | None = None


@dataclass(frozen=True)
class HubTopic:
    """One approved Тема as the Хаб shows it: its cover and one cell per Площадка.
    `number` is the Тема's 1-based position among the Хаб's Темы."""

    id: PlanItemId
    number: int
    title: str
    cover: HubCellState
    has_cover: bool
    articles: Sequence[HubArticleCell]

    @property
    def cells(self) -> list[HubCellState]:
        return [self.cover, *(cell.state for cell in self.articles)]

    @property
    def finished(self) -> bool:
        """Nothing left ⏳ - every cell is ✅ or ❌, so the Тема's result card is worth opening."""
        return "pending" not in self.cells

    @property
    def has_failures(self) -> bool:
        return "failed" in self.cells


@dataclass(frozen=True)
class PlanHubView:
    """An approved Plan as its Хаб (#91): progress per Тема, derived from `articles` and
    `plan_items` cover state at read time. `open_item_id` is the Тема whose result card the
    Plan message currently shows, `None` for the checklist."""

    id: PlanId
    week_label: str
    status: str
    topics: Sequence[HubTopic]
    open_item_id: PlanItemId | None = None

    @property
    def open_topic(self) -> HubTopic | None:
        return next((t for t in self.topics if t.id == self.open_item_id), None)

    @property
    def cells(self) -> list[HubCellState]:
        return [cell for topic in self.topics for cell in topic.cells]


@dataclass(frozen=True)
class TopicDraft:
    """A Тема ready to be stored, either from automatic Wordstat sourcing or a manual proposal."""

    title: str
    summary: str = ""
    keywords: Sequence[str] = field(default_factory=tuple)


@dataclass(frozen=True)
class GeneratedVersion:
    """One generation run of an Article: its own prompt, model, and cost (ADR-driven Версия).

    `tokens`/`cost` are `None` when any of the run's steps didn't report usage or had no
    pricing configured (#74) - a real number here is always a complete one; an unknown
    component never gets silently folded into a `0`."""

    content: str
    prompt: str
    model: str
    tokens: int | None
    cost: float | None
    source_job_id: int | None = None
    # `research_status` from the Job output (#94) - metadata, never part of `content`.
    research_status: str | None = None


@dataclass(frozen=True)
class ArticleVersionSummary:
    """One Версия's metadata only, for /history's version list (#26) - no content, so the
    list stays cheap even for an Article with many regenerations."""

    id: int
    model: str
    tokens: int | None
    cost: float | None
    created_at: datetime


@dataclass(frozen=True)
class ArticleVersionView:
    """One Версия's full content, for /history's version detail screen (#26)."""

    id: int
    content: str
    model: str
    tokens: int | None
    cost: float | None
    created_at: datetime
