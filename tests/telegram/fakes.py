from __future__ import annotations

from typing import Any

from content_zavod.job_queue import JobResult, JobSnapshot
from content_zavod.telegram.gateway import SentPhoto
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


class FakeJobs:
    """`JobQueue.enqueue`/`get_job` (#113): idempotent by key, ids from 1; `finish` stores an
    output the way the worker would and returns the notification's `JobResult`."""

    def __init__(self) -> None:
        self.jobs: dict[int, JobSnapshot] = {}
        self.keys: dict[str, int] = {}

    async def enqueue(self, job_type: str, payload: dict[str, Any], idempotency_key: str) -> int:
        if idempotency_key in self.keys:
            return self.keys[idempotency_key]
        job_id = len(self.jobs) + 1
        self.jobs[job_id] = JobSnapshot(job_id, job_type, "queued", payload)
        self.keys[idempotency_key] = job_id
        return job_id

    async def get_job(self, job_id: int) -> JobSnapshot | None:
        return self.jobs.get(job_id)

    def finish(self, job_id: int, output: dict[str, Any] | None) -> JobResult:
        job = self.jobs[job_id]
        status = "done" if output is not None else "failed"
        self.jobs[job_id] = JobSnapshot(job_id, job.job_type, status, job.payload, output)
        return JobResult(job_id=job_id, job_type=job.job_type, status=status, output=output)


class RecordingBot:
    """`BotClient` that records every call; message ids count up from 100."""

    def __init__(self) -> None:
        self.sent: list[tuple[int, str, object, str | None]] = []
        self.edited: list[tuple[int, int, str, object]] = []
        self.deleted: list[tuple[int, int]] = []
        self.photos: list[tuple[int, object, str | None, object]] = []
        self.edited_media: list[tuple[int, int, object, str | None, object]] = []
        self.pinned: list[tuple[int, int]] = []
        self.fail_edits = False
        self.fail_pins = False
        self._next_id = 100

    async def send_message(self, chat_id, text, reply_markup=None, parse_mode=None) -> int:
        self.sent.append((chat_id, text, reply_markup, parse_mode))
        message_id = self._next_id
        self._next_id += 1
        return message_id

    async def send_document(self, chat_id, document, caption=None) -> None:
        pass

    async def send_photo(self, chat_id, photo, caption=None, reply_markup=None) -> SentPhoto:
        self.photos.append((chat_id, photo, caption, reply_markup))
        message_id = self._next_id
        self._next_id += 1
        return SentPhoto(message_id, f"file-{message_id}")

    async def edit_message_media(
        self, chat_id, message_id, photo, caption=None, reply_markup=None
    ) -> str | None:
        if self.fail_edits:
            raise RuntimeError("message to edit not found")
        self.edited_media.append((chat_id, message_id, photo, caption, reply_markup))
        return f"file-edit-{message_id}"

    async def pin_chat_message(self, chat_id, message_id) -> None:
        if self.fail_pins:
            raise RuntimeError("not enough rights to manage pinned messages")
        self.pinned.append((chat_id, message_id))

    async def edit_message_text(self, chat_id, message_id, text, reply_markup=None) -> None:
        if self.fail_edits:
            raise RuntimeError("message to edit not found")
        self.edited.append((chat_id, message_id, text, reply_markup))

    async def edit_message_reply_markup(self, chat_id, message_id, reply_markup=None) -> None:
        pass

    async def delete_message(self, chat_id, message_id) -> None:
        self.deleted.append((chat_id, message_id))

    async def set_my_commands(self, commands, *, scope) -> None:
        pass


def button_texts(markup) -> list[list[str]]:
    return [[button.text for button in row] for row in markup.inline_keyboard]


def button_data(markup) -> list[list[str | None]]:
    return [
        [button.callback_data or button.url for button in row] for row in markup.inline_keyboard
    ]
