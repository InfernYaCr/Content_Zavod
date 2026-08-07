"""Shared value types for the domain layer: identifiers and view DTOs.

`PlanView`/`PlanItemView`/`ArticleView` are the consumer contract the
Telegram layer (#4) was built against — keep their shape stable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, NewType, Sequence

PlanId = NewType("PlanId", str)
PlanItemId = NewType("PlanItemId", str)
ArticleId = NewType("ArticleId", str)

PlanStatus = Literal["pending_review", "approved"]
PlanItemStatus = Literal["pending_review", "approved", "rejected"]
ArticleStatus = Literal["queued", "generating", "error", "ready", "regenerating", "exported"]


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
    title: str
    platform: str
    filename: str
    content: bytes


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
