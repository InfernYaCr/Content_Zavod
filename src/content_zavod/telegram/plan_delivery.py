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
"""

from __future__ import annotations

from typing import Protocol

from .gateway import TelegramGateway
from .types import PlanId, PlanMessageRef, PlanView


class PlanMessageRefs(Protocol):
    async def get_message_ref(self, plan_id: PlanId) -> PlanMessageRef | None: ...

    async def record_message_ref(self, plan_id: PlanId, chat_id: int, message_id: int) -> None: ...


async def deliver_plan_message(
    plan: PlanMessageRefs, gateway: TelegramGateway, chat_id: int, view: PlanView
) -> None:
    ref = await plan.get_message_ref(view.id)
    if ref is not None:
        await gateway.edit_plan(ref.chat_id, ref.message_id, view)
        return
    message_id = await gateway.send_plan(chat_id, view)
    await plan.record_message_ref(view.id, chat_id, message_id)
