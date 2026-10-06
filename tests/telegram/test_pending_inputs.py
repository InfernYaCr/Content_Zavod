"""`PendingInputs` against real Postgres (#88) - the SQL side of what `fakes.FakePendingInputs`
stands in for in the flow/dispatcher unit tests, plus the TTL the fake leaves out."""

from __future__ import annotations

from collections.abc import AsyncIterator

import asyncpg
import pytest_asyncio

from content_zavod.telegram.pending_inputs import PendingInput, PendingInputs

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
    await pending_inputs.put(1, 10, WAIT)

    assert await pending_inputs.get(1, 10) == WAIT
    assert await pending_inputs.get(1, 99) is None
    assert await pending_inputs.get(2, 10) is None


async def test_put_overwrites_the_previous_wait_of_any_kind(pending_inputs: PendingInputs) -> None:
    newer = PendingInput("article_comment", "article-1", 200, 201)
    assert await pending_inputs.put(1, 10, WAIT) is None
    assert await pending_inputs.put(1, 10, newer) == WAIT

    assert await pending_inputs.get(1, 10) == newer


async def test_put_returns_an_expired_wait_it_overwrites(
    pending_inputs: PendingInputs, pool: asyncpg.Pool
) -> None:
    """Its prompt messages are still in the chat, so the caller needs them to tidy up."""
    await pending_inputs.put(1, 10, WAIT)
    await _expire(pool)

    assert await pending_inputs.put(1, 10, WAIT) == WAIT


async def test_a_private_chat_wait_has_no_force_reply_message(
    pending_inputs: PendingInputs,
) -> None:
    private = PendingInput("plan_item_comment", "item-1", prompt_message_id=100)
    await pending_inputs.put(10, 10, private)

    assert await pending_inputs.get(10, 10) == private
    assert await pending_inputs.take(10, 10, "plan_item_comment", reply_to_message_id=55) is None
    assert (
        await pending_inputs.take(10, 10, "plan_item_comment", reply_to_message_id=100) == private
    )


async def test_take_removes_the_wait_once(pending_inputs: PendingInputs) -> None:
    await pending_inputs.put(1, 10, WAIT)

    assert await pending_inputs.take(1, 10, "plan_item_comment") == WAIT
    assert await pending_inputs.take(1, 10, "plan_item_comment") is None
    assert await pending_inputs.get(1, 10) is None


async def test_take_leaves_a_wait_that_does_not_match(pending_inputs: PendingInputs) -> None:
    await pending_inputs.put(1, 10, WAIT)

    assert await pending_inputs.take(1, 10, "article_comment") is None
    assert await pending_inputs.take(1, 10, "plan_item_comment", target_id="item-2") is None
    assert await pending_inputs.take(1, 10, "plan_item_comment", reply_to_message_id=55) is None

    assert await pending_inputs.get(1, 10) == WAIT


async def test_take_accepts_a_reply_to_either_prompt_message(pending_inputs: PendingInputs) -> None:
    await pending_inputs.put(1, 10, WAIT)
    assert await pending_inputs.take(1, 10, "plan_item_comment", reply_to_message_id=101) == WAIT

    await pending_inputs.put(1, 10, WAIT)
    assert await pending_inputs.take(1, 10, "plan_item_comment", reply_to_message_id=100) == WAIT


async def test_take_with_matching_target(pending_inputs: PendingInputs) -> None:
    await pending_inputs.put(1, 10, WAIT)

    assert await pending_inputs.take(1, 10, "plan_item_comment", target_id="item-1") == WAIT


async def test_an_expired_wait_is_gone(pending_inputs: PendingInputs, pool: asyncpg.Pool) -> None:
    await pending_inputs.put(1, 10, WAIT)
    await _expire(pool)

    assert await pending_inputs.get(1, 10) is None
    assert await pending_inputs.take(1, 10, "plan_item_comment") is None


async def test_put_over_an_expired_wait_starts_a_fresh_ttl(
    pending_inputs: PendingInputs, pool: asyncpg.Pool
) -> None:
    await pending_inputs.put(1, 10, WAIT)
    await _expire(pool)

    await pending_inputs.put(1, 10, WAIT)

    assert await pending_inputs.get(1, 10) == WAIT
