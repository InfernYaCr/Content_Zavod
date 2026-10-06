from __future__ import annotations

from content_zavod.telegram.pending_inputs import PendingInput


class FakePendingInputs:
    """In-memory `PendingInputs` (#88): same one-row-per-(chat_id, user_id) and `take`
    matching rules as the SQL, minus the TTL (covered by tests/telegram/test_pending_inputs.py)."""

    def __init__(self) -> None:
        self.rows: dict[tuple[int, int], PendingInput] = {}

    async def put(self, chat_id: int, user_id: int, pending: PendingInput) -> PendingInput | None:
        previous = self.rows.get((chat_id, user_id))
        self.rows[(chat_id, user_id)] = pending
        return previous

    async def get(self, chat_id: int, user_id: int) -> PendingInput | None:
        return self.rows.get((chat_id, user_id))

    async def take(
        self,
        chat_id: int,
        user_id: int,
        kind: str,
        *,
        target_id: str | None = None,
        reply_to_message_id: int | None = None,
    ) -> PendingInput | None:
        pending = self.rows.get((chat_id, user_id))
        if (
            pending is None
            or pending.kind != kind
            or (target_id is not None and pending.target_id != target_id)
            or (
                reply_to_message_id is not None
                and reply_to_message_id
                not in (pending.prompt_message_id, pending.force_reply_message_id)
            )
        ):
            return None
        return self.rows.pop((chat_id, user_id))
