"""Исследование Темы (#94): the evidence bundle every Статья of a Тема is written from,
and the Postgres cache that makes it a once-per-Тема artifact.

An `Evidence` item is one fact the model extracted from a fetched page, kept only if its
`quote` is found verbatim in that page's text - the quote is the proof, the URL is the
Источник. `TopicResearch` pairs the bundle with the shared outline built from it; both
are produced once per Тема by `pipelines.topic_research` and reused by every Площадка and
every Перегенерация through `TopicResearchStore`.

Written by the worker (like `GenerationSteps`), never by the notification path: it is a
cache of generation inputs, not Plan/Article state (ADR-0004).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

import asyncpg

from .types import PlanItemId

# `ok` - at least one fact survived quote verification;
# `no_evidence` - search worked but nothing usable was found/verified;
# `search_unavailable` - the search provider failed or isn't configured.
ResearchStatus = Literal["ok", "no_evidence", "search_unavailable"]


@dataclass(frozen=True)
class ResearchSource:
    """One fetched page that contributed at least one verified fact."""

    url: str
    title: str
    publisher: str
    published_at: str | None
    retrieved_at: str
    excerpt_hash: str


@dataclass(frozen=True)
class Evidence:
    """One fact, backed by a verbatim `quote` from the page at `url`."""

    id: str
    fact: str
    quote: str
    url: str


@dataclass(frozen=True)
class ResearchBundle:
    query: str
    status: ResearchStatus
    sources: Sequence[ResearchSource] = field(default_factory=tuple)
    evidence: Sequence[Evidence] = field(default_factory=tuple)

    @property
    def has_evidence(self) -> bool:
        return bool(self.evidence)

    def source_for(self, url: str) -> ResearchSource | None:
        return next((source for source in self.sources if source.url == url), None)

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "status": self.status,
            "sources": [asdict(source) for source in self.sources],
            "evidence": [asdict(item) for item in self.evidence],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ResearchBundle:
        return cls(
            query=data["query"],
            status=data["status"],
            sources=tuple(ResearchSource(**source) for source in data["sources"]),
            evidence=tuple(Evidence(**item) for item in data["evidence"]),
        )


@dataclass(frozen=True)
class TopicResearch:
    bundle: ResearchBundle
    outline: str


def topic_fingerprint(title: str, summary: str, keywords: Sequence[str]) -> str:
    """Identifies the Тема's brief a cached Исследование was made for - an edited Тема
    (new title/summary/keywords) must not reuse research done for its old wording."""
    raw = json.dumps([title, summary, list(keywords)], ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


class TopicResearchStore:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def get(self, plan_item_id: PlanItemId, fingerprint: str) -> TopicResearch | None:
        row = await self._pool.fetchrow(
            "SELECT bundle, outline FROM topic_research WHERE plan_item_id = $1 "
            "AND fingerprint = $2",
            plan_item_id,
            fingerprint,
        )
        if row is None:
            return None
        return TopicResearch(
            bundle=ResearchBundle.from_dict(json.loads(row["bundle"])), outline=row["outline"]
        )

    async def put(
        self, plan_item_id: PlanItemId, fingerprint: str, research: TopicResearch
    ) -> None:
        await self._pool.execute(
            """
            INSERT INTO topic_research (plan_item_id, fingerprint, status, bundle, outline)
            VALUES ($1, $2, $3, $4::jsonb, $5)
            ON CONFLICT (plan_item_id) DO UPDATE
            SET fingerprint = EXCLUDED.fingerprint, status = EXCLUDED.status,
                bundle = EXCLUDED.bundle, outline = EXCLUDED.outline, updated_at = now()
            """,
            plan_item_id,
            fingerprint,
            research.bundle.status,
            json.dumps(research.bundle.to_dict(), ensure_ascii=False),
            research.outline,
        )
