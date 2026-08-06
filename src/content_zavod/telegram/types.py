from dataclasses import dataclass
from typing import NewType, Sequence

PlanId = NewType("PlanId", str)
PlanItemId = NewType("PlanItemId", str)


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
    title: str
    platform: str
    filename: str
    content: bytes
