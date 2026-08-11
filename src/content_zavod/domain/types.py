"""Shared value types for the domain layer: identifiers and view DTOs.

`PlanView`/`PlanItemView`/`ArticleView` are the consumer contract the
Telegram layer (#4) was built against — keep their shape stable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, NewType, Sequence

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
class TopicDraft:
    """A Тема ready to be stored, either from automatic Wordstat sourcing or a manual proposal."""

    title: str
    summary: str = ""
    keywords: Sequence[str] = field(default_factory=tuple)


@dataclass(frozen=True)
class GeneratedVersion:
    """One generation run of an Article: its own prompt, model, and cost (ADR-driven Версия)."""

    content: str
    prompt: str
    model: str
    tokens: int
    cost: float


@dataclass(frozen=True)
class ArticleVersionSummary:
    """One Версия's metadata only, for /history's version list (#26) - no content, so the
    list stays cheap even for an Article with many regenerations."""

    id: int
    model: str
    tokens: int
    cost: float
    created_at: datetime


@dataclass(frozen=True)
class ArticleVersionView:
    """One Версия's full content, for /history's version detail screen (#26)."""

    id: int
    content: str
    model: str
    tokens: int
    cost: float
    created_at: datetime
