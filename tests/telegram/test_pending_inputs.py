"""`PendingInputs` against real Postgres (#88) - the SQL side of what `fakes.FakePendingInputs`
stands in for in the flow/dispatcher unit tests, plus the TTL the fake leaves out."""

from __future__ import annotations

from collections.abc import AsyncIterator

import asyncpg
import pytest_asyncio

from content_zavod.telegram.pending_inputs import PendingInput, PendingInputs
from tests.tenancy import WS_A, WS_B

WAIT = PendingInput(
    kind="plan_item_comment", target_id="item-1", prompt_message_id=100, force_reply_message_id=101
)


@pytest_asyncio.fixture(loop_scope="session")
async def pending_inputs(pool: asyncpg.Pool) -> AsyncIterator[PendingInputs]:
    await pool.execute("TRUNCATE TABLE pending_inputs")
    yield PendingInputs(pool)


async def _expire(pool: asyncpg.Pool) -> None:
    await pool.execute("UPDATE pending_inputs SET created_at = now() - interval '31 minutes'")


async def test_put_then_get_roundtrips(pending_inputs: PendingInputs) -> None:
    await pending_inputs.put(WS_A, 1, 10, WAIT)

    assert await pending_inputs.get(WS_A, 1, 10) == WAIT
    assert await pending_inputs.get(WS_A, 1, 99) is None
    assert await pending_inputs.get(WS_A, 2, 10) is None


async def test_put_overwrites_the_previous_wait_of_any_kind(pending_inputs: PendingInputs) -> None:
    newer = PendingInput("article_comment", "article-1", 200, 201)
    assert await pending_inputs.put(WS_A, 1, 10, WAIT) is None
    assert await pending_inputs.put(WS_A, 1, 10, newer) == WAIT

    assert await pending_inputs.get(WS_A, 1, 10) == newer


async def test_put_returns_an_expired_wait_it_overwrites(
    pending_inputs: PendingInputs, pool: asyncpg.Pool
) -> None:
    """Its prompt messages are still in the chat, so the caller needs them to tidy up."""
    await pending_inputs.put(WS_A, 1, 10, WAIT)
    await _expire(pool)

    assert await pending_inputs.put(WS_A, 1, 10, WAIT) == WAIT


async def test_a_private_chat_wait_has_no_force_reply_message(
    pending_inputs: PendingInputs,
) -> None:
    private = PendingInput("plan_item_comment", "item-1", prompt_message_id=100)
    await pending_inputs.put(WS_A, 10, 10, private)

    assert await pending_inputs.get(WS_A, 10, 10) == private
    assert (
        await pending_inputs.take(WS_A, 10, 10, "plan_item_comment", reply_to_message_id=55) is None
    )
    assert (
        await pending_inputs.take(WS_A, 10, 10, "plan_item_comment", reply_to_message_id=100)
        == private
    )


async def test_take_removes_the_wait_once(pending_inputs: PendingInputs) -> None:
    await pending_inputs.put(WS_A, 1, 10, WAIT)

    assert await pending_inputs.take(WS_A, 1, 10, "plan_item_comment") == WAIT
    assert await pending_inputs.take(WS_A, 1, 10, "plan_item_comment") is None
    assert await pending_inputs.get(WS_A, 1, 10) is None


async def test_take_leaves_a_wait_that_does_not_match(pending_inputs: PendingInputs) -> None:
    await pending_inputs.put(WS_A, 1, 10, WAIT)

    assert await pending_inputs.take(WS_A, 1, 10, "article_comment") is None
    assert await pending_inputs.take(WS_A, 1, 10, "plan_item_comment", target_id="item-2") is None
    assert (
        await pending_inputs.take(WS_A, 1, 10, "plan_item_comment", reply_to_message_id=55) is None
    )

    assert await pending_inputs.get(WS_A, 1, 10) == WAIT


async def test_take_accepts_a_reply_to_either_prompt_message(pending_inputs: PendingInputs) -> None:
    await pending_inputs.put(WS_A, 1, 10, WAIT)
    assert (
        await pending_inputs.take(WS_A, 1, 10, "plan_item_comment", reply_to_message_id=101) == WAIT
    )

    await pending_inputs.put(WS_A, 1, 10, WAIT)
    assert (
        await pending_inputs.take(WS_A, 1, 10, "plan_item_comment", reply_to_message_id=100) == WAIT
    )


async def test_take_with_matching_target(pending_inputs: PendingInputs) -> None:
    await pending_inputs.put(WS_A, 1, 10, WAIT)

    assert await pending_inputs.take(WS_A, 1, 10, "plan_item_comment", target_id="item-1") == WAIT


async def test_an_expired_wait_is_gone(pending_inputs: PendingInputs, pool: asyncpg.Pool) -> None:
    await pending_inputs.put(WS_A, 1, 10, WAIT)
    await _expire(pool)

    assert await pending_inputs.get(WS_A, 1, 10) is None
    assert await pending_inputs.take(WS_A, 1, 10, "plan_item_comment") is None


async def test_put_over_an_expired_wait_starts_a_fresh_ttl(
    pending_inputs: PendingInputs, pool: asyncpg.Pool
) -> None:
    await pending_inputs.put(WS_A, 1, 10, WAIT)
    await _expire(pool)

    await pending_inputs.put(WS_A, 1, 10, WAIT)

    assert await pending_inputs.get(WS_A, 1, 10) == WAIT


async def test_a_wait_of_one_workspace_is_invisible_to_another(
    pending_inputs: PendingInputs,
) -> None:
    other = PendingInput("article_comment", "article-b", 300)
    await pending_inputs.put(WS_A, 1, 10, WAIT)
    assert await pending_inputs.put(WS_B, 1, 10, other) is None

    assert await pending_inputs.take(WS_B, 1, 10, WAIT.kind) is None
    assert await pending_inputs.get(WS_A, 1, 10) == WAIT
    assert await pending_inputs.take(WS_B, 1, 10, other.kind) == other
    assert await pending_inputs.get(WS_A, 1, 10) == WAIT


async def test_touch_renews_only_a_live_wait_of_that_kind_in_its_own_workspace(
    pool: asyncpg.Pool, pending_inputs: PendingInputs
) -> None:
    """#134: a renewal is an UPDATE - it never brings back a wait someone has just taken."""
    await pending_inputs.put(WS_A, 1, 10, WAIT)
    await pool.execute("UPDATE pending_inputs SET created_at = now() - interval '20 minutes'")

    assert not await pending_inputs.touch(WS_B, 1, 10, WAIT.kind)
    assert not await pending_inputs.touch(WS_A, 1, 10, "other_kind")
    assert await pending_inputs.touch(WS_A, 1, 10, WAIT.kind)
    age = await pool.fetchval("SELECT now() - created_at FROM pending_inputs")
    assert age.total_seconds() < 60

    await pending_inputs.take(WS_A, 1, 10, WAIT.kind)
    assert not await pending_inputs.touch(WS_A, 1, 10, WAIT.kind)
    assert await pending_inputs.get(WS_A, 1, 10) is None

    await pending_inputs.put(WS_A, 1, 10, WAIT)
    await _expire(pool)
    assert not await pending_inputs.touch(WS_A, 1, 10, WAIT.kind)  # expired stays expired
