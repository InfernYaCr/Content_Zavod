import asyncpg

from content_zavod.domain import (
    Evidence,
    Plan,
    ResearchBundle,
    ResearchSource,
    TopicDraft,
    TopicResearch,
    TopicResearchStore,
    topic_fingerprint,
)

_RESEARCH = TopicResearch(
    bundle=ResearchBundle(
        query="Как выбрать CRM",
        status="ok",
        sources=(
            ResearchSource(
                url="https://media.example/a",
                title="Опрос",
                publisher="Медиа",
                published_at=None,
                retrieved_at="2026-10-06T00:00:00+00:00",
                excerpt_hash="abc",
            ),
        ),
        evidence=(
            Evidence(
                id="E1",
                fact="42% компаний используют CRM",
                quote="42% компаний используют CRM.",
                url="https://media.example/a",
            ),
        ),
    ),
    outline="## Аутлайн [E1]",
)


async def _item(plan: Plan):
    plan_id = await plan.add_topics("Week 1", [TopicDraft(title="Как выбрать CRM")])
    return (await plan.get(plan_id)).items[0].id


async def test_put_then_get_round_trips_the_research(pool: asyncpg.Pool, plan: Plan) -> None:
    store = TopicResearchStore(pool)
    item_id = await _item(plan)
    fingerprint = topic_fingerprint("Как выбрать CRM", "", [])

    await store.put(item_id, fingerprint, _RESEARCH)

    assert await store.get(item_id, fingerprint) == _RESEARCH


async def test_get_misses_for_another_fingerprint_and_put_overwrites(
    pool: asyncpg.Pool, plan: Plan
) -> None:
    store = TopicResearchStore(pool)
    item_id = await _item(plan)
    old, new = topic_fingerprint("A", "", []), topic_fingerprint("B", "", [])
    await store.put(item_id, old, _RESEARCH)

    assert await store.get(item_id, new) is None

    replacement = TopicResearch(
        bundle=ResearchBundle(query="B", status="no_evidence"), outline="другой"
    )
    await store.put(item_id, new, replacement)

    assert await store.get(item_id, new) == replacement
    assert await store.get(item_id, old) is None


def test_fingerprint_changes_with_the_topic_brief() -> None:
    base = topic_fingerprint("Тема", "описание", ["a"])

    assert base == topic_fingerprint("Тема", "описание", ["a"])
    assert base != topic_fingerprint("Тема", "описание", ["b"])
    assert base != topic_fingerprint("Тема 2", "описание", ["a"])
