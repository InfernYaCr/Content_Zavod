"""deliver_plan_message: send-or-edit the one canonical Plan message (ADR-0005, #73).

Shared by the `generate_plan` notification handler and the manual `/topic`
command - both add Topics to the same still-open Plan, and both used to post
an unconditional new message every time, which could put more than one
message for the same Plan in the chat even with no crash involved (a second
`/topic` while the week's Plan was still `pending_review`). Storing the
Plan's canonical Telegram identity (chat_id/message_id, see
`domain.plan.Plan.get_message_ref`/`record_message_ref`) closes that gap:
the first delivery sends and records it, every later delivery for the same
Plan edits that recorded message instead.

The identity is recorded immediately after a successful send, inside this
same call - well before a caller's own delivered-mark (e.g.
`job_queue.run_notifications`'s `notified_at`) is set. That closes the
specific crash window #73 is about: a crash between a successful Telegram
send and the delivered-mark used to leave the Plan un-marked, so a retry
replayed the whole handler and sent a second message. Now the ref is already
durable by the time that window opens, so the retry edits instead.

This narrows rather than eliminates the underlying gap: the single await
between `gateway.send_plan` returning and `plan.record_message_ref`
committing is not atomic with the Telegram call, so a crash in that exact
instant can still duplicate a message on retry. Closing that fully would
need exactly-once delivery from Telegram itself, which #73's own issue text
already accepts isn't available ("Telegram не даёт exactly-once"). Two
deliveries racing each other for the same not-yet-recorded Plan (as opposed
to one retrying after the other) can also both send before either records -
out of scope here since `run_notifications` delivers one result at a time;
only a delivery outside that loop (e.g. `/topic`) running concurrently with
it could still race.

A recorded message someone deleted in the chat (#106) would otherwise fail
every later edit for that Plan. An edit that finds it gone (`MessageGone`)
sends a fresh message and moves the ref to it with
`replace_message_ref`, conditional on the ref still naming the dead
message. Two deliveries racing after the same deletion therefore both
send, but only one replacement sticks: the loser deletes its own extra
message (best effort) and redraws the winner's with its view instead.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Protocol

from .gateway import MessageGone, TelegramGateway
from .types import PlanHubView, PlanId, PlanMessageRef, PlanView

logger = logging.getLogger(__name__)


class PlanMessageRefs(Protocol):
    async def get_message_ref(self, plan_id: PlanId) -> PlanMessageRef | None: ...

    async def record_message_ref(self, plan_id: PlanId, chat_id: int, message_id: int) -> None: ...

    async def replace_message_ref(
        self, plan_id: PlanId, old: PlanMessageRef, chat_id: int, message_id: int
    ) -> bool: ...


async def _edit_or_replace(
    plan: PlanMessageRefs,
    gateway: TelegramGateway,
    plan_id: PlanId,
    chat_id: int,
    ref: PlanMessageRef,
    *,
    send: Callable[[int], Awaitable[int]],
    edit: Callable[[int, int], Awaitable[None]],
) -> bool:
    """Edits the recorded Plan message; if it is gone (#106), sends a replacement into
    `chat_id` and makes it canonical. Returns whether the replacement this call sent is now
    the Plan message."""
    try:
        await edit(ref.chat_id, ref.message_id)
        return False
    except MessageGone:
        logger.warning(
            "Plan %s message %s in chat %s is gone, sending a new one",
            plan_id,
            ref.message_id,
            ref.chat_id,
        )
    message_id = await send(chat_id)
    if await plan.replace_message_ref(plan_id, ref, chat_id, message_id):
        return True
    # Another delivery replaced the dead message first: ours is an extra - tidy it up and draw
    # this delivery's state on the winner instead.
    try:
        await gateway.delete_message(chat_id, message_id)
    except Exception:
        logger.warning(
            "could not delete extra Plan %s message %s in chat %s",
            plan_id,
            message_id,
            chat_id,
            exc_info=True,
        )
    current = await plan.get_message_ref(plan_id)
    if current is not None:
        await edit(current.chat_id, current.message_id)
    return False


async def deliver_plan_message(
    plan: PlanMessageRefs, gateway: TelegramGateway, chat_id: int, view: PlanView
) -> bool:
    """Returns whether a new message was sent - `False` means an earlier, possibly long
    scrolled-away message was edited in place, which an interactive caller may want to
    point the user at. A recorded message deleted in the chat is replaced by a new one, which
    counts as sent (#106)."""
    ref = await plan.get_message_ref(view.id)
    if ref is not None:
        return await _edit_or_replace(
            plan,
            gateway,
            view.id,
            chat_id,
            ref,
            send=lambda c: gateway.send_plan(c, view),
            edit=lambda c, m: gateway.edit_plan(c, m, view),
        )
    message_id = await gateway.send_plan(chat_id, view)
    await plan.record_message_ref(view.id, chat_id, message_id)
    return True


class PlanHubSource(PlanMessageRefs, Protocol):
    async def get_hub(self, plan_id: PlanId) -> PlanHubView: ...


async def deliver_plan_hub(
    plan: PlanHubSource, gateway: TelegramGateway, chat_id: int, plan_id: PlanId
) -> None:
    """Redraws an approved Plan's canonical message as its Хаб (#91), from statuses read
    right now - so a redelivered or out-of-order notification just redraws the same state.
    Same send-once/edit-after shape as `deliver_plan_message`: a Plan with no recorded message
    yet gets one sent and recorded, and a deleted one is replaced (#106). A Plan that is no
    longer `approved` (archived by a replacement) is left alone - its late Job results have
    nowhere meaningful to show."""
    hub = await plan.get_hub(plan_id)
    if hub.status != "approved":
        return
    ref = await plan.get_message_ref(plan_id)
    if ref is not None:
        await _edit_or_replace(
            plan,
            gateway,
            plan_id,
            chat_id,
            ref,
            send=lambda c: gateway.send_hub(c, hub),
            edit=lambda c, m: gateway.edit_hub(c, m, hub),
        )
        return
    message_id = await gateway.send_hub(chat_id, hub)
    await plan.record_message_ref(plan_id, chat_id, message_id)
