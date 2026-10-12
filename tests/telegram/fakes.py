from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from content_zavod.job_queue import JobResult, JobSnapshot
from content_zavod.telegram.gateway import SentPhoto
from content_zavod.telegram.pending_inputs import PendingInput
from content_zavod.workspaces import LimitRefused, Usage
from tests.tenancy import WS_A


class FakePendingInputs:
    """In-memory `PendingInputs` (#88): same one-row-per-(workspace_id, chat_id, user_id) and
    `take` matching rules as the SQL, minus the TTL (covered by
    tests/telegram/test_pending_inputs.py). `rows` is keyed by (chat_id, user_id) for the
    default Workspace A; other Workspaces' rows live in `by_workspace`."""

    def __init__(self) -> None:
        self.by_workspace: dict[int, dict[tuple[int, int], PendingInput]] = {WS_A: {}}
        self.touched = 0

    @property
    def rows(self) -> dict[tuple[int, int], PendingInput]:
        return self.by_workspace[WS_A]

    def _rows(self, workspace_id: int) -> dict[tuple[int, int], PendingInput]:
        return self.by_workspace.setdefault(workspace_id, {})

    async def put(
        self, workspace_id: int, chat_id: int, user_id: int, pending: PendingInput
    ) -> PendingInput | None:
        rows = self._rows(workspace_id)
        previous = rows.get((chat_id, user_id))
        rows[(chat_id, user_id)] = pending
        return previous

    async def get(self, workspace_id: int, chat_id: int, user_id: int) -> PendingInput | None:
        return self._rows(workspace_id).get((chat_id, user_id))

    async def take(
        self,
        workspace_id: int,
        chat_id: int,
        user_id: int,
        kind: str,
        *,
        target_id: str | None = None,
        reply_to_message_id: int | None = None,
    ) -> PendingInput | None:
        rows = self._rows(workspace_id)
        pending = rows.get((chat_id, user_id))
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
        return rows.pop((chat_id, user_id))

    async def touch(self, workspace_id: int, chat_id: int, user_id: int, kind: str) -> bool:
        pending = self._rows(workspace_id).get((chat_id, user_id))
        live = pending is not None and pending.kind == kind
        self.touched += live
        return live


class FakeJobs:
    """`JobQueue.enqueue`/`get_job`/`drop_payload_keys`: idempotent by key, ids from 1, every
    Job of the Пространство it was enqueued in; `finish` stores an output the way the worker
    would and returns the notification's `JobResult`."""

    def __init__(self) -> None:
        self.jobs: dict[int, JobSnapshot] = {}
        self.workspaces: dict[int, int] = {}
        self.keys: dict[tuple[int, str], int] = {}

    async def enqueue(
        self,
        workspace_id: int,
        job_type: str,
        payload: dict[str, Any],
        idempotency_key: str,
        *,
        conn: Any = None,
    ) -> int:
        if (workspace_id, idempotency_key) in self.keys:
            return self.keys[(workspace_id, idempotency_key)]
        job_id = len(self.jobs) + 1
        self.jobs[job_id] = JobSnapshot(job_id, job_type, "queued", payload)
        self.workspaces[job_id] = workspace_id
        self.keys[(workspace_id, idempotency_key)] = job_id
        return job_id

    async def get_job(self, workspace_id: int, job_id: int) -> JobSnapshot | None:
        if self.workspaces.get(job_id) != workspace_id:
            return None
        return self.jobs.get(job_id)

    async def drop_payload_keys(self, workspace_id: int, job_id: int, keys: list[str]) -> None:
        job = self.jobs.get(job_id)
        if job is None or self.workspaces[job_id] != workspace_id or job.status == "queued":
            return
        payload = {key: value for key, value in job.payload.items() if key not in keys}
        self.jobs[job_id] = JobSnapshot(job_id, job.job_type, job.status, payload, job.output)

    def finish(self, job_id: int, output: dict[str, Any] | None) -> JobResult:
        job = self.jobs[job_id]
        status = "done" if output is not None else "failed"
        self.jobs[job_id] = JobSnapshot(job_id, job.job_type, status, job.payload, output)
        return JobResult(
            workspace_id=self.workspaces[job_id],
            job_id=job_id,
            job_type=job.job_type,
            status=status,
            output=output,
        )


class RecordingBot:
    """`BotClient` that records every call; message ids count up from 100."""

    def __init__(self) -> None:
        self.sent: list[tuple[int, str, object, str | None]] = []
        self.edited: list[tuple[int, int, str, object]] = []
        self.deleted: list[tuple[int, int]] = []
        self.photos: list[tuple[int, object, str | None, object]] = []
        self.edited_media: list[tuple[int, int, object, str | None, object]] = []
        self.pinned: list[tuple[int, int]] = []
        self.markups: list[tuple[int, int, object]] = []
        self.fail_edits = False
        self.fail_pins = False
        self._next_id = 100

    async def send_message(self, chat_id, text, reply_markup=None) -> int:
        self.sent.append((chat_id, text, reply_markup, None))
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
        self.markups.append((chat_id, message_id, reply_markup))

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


class FakeReservation:
    conn = None

    def __init__(self, limits: FakeLimits) -> None:
        self._limits = limits

    def require(self, job_type: str, count: int = 1) -> None:
        self._limits.required.append((job_type, count))
        if self._limits.short is not None:
            raise self._limits.short


class FakeLimits:
    """`Limits` in memory: lets everything through, or raises `refusal` from every check
    (`short` - only from `Reservation.require`). `reserved`/`required`/`prechecked` - the
    (job type, count) each check asked for."""

    def __init__(self, usage: Usage | None = None) -> None:
        self.usage_value = usage or Usage(
            plans_used=0, plan_limit=1, articles_used=0, article_limit=8
        )
        self.refusal: LimitRefused | None = None
        self.short: LimitRefused | None = None
        self.reserved: list[tuple[str | None, int]] = []
        self.required: list[tuple[str, int]] = []
        self.prechecked: list[str] = []

    async def usage(self, workspace_id: int) -> Usage:
        return self.usage_value

    async def precheck(self, workspace_id: int, job_type: str) -> None:
        self.prechecked.append(job_type)
        if self.refusal is not None:
            raise self.refusal

    @asynccontextmanager
    async def reserve(
        self, workspace_id: int, job_type: str | None = None, count: int = 1
    ) -> AsyncIterator[FakeReservation]:
        if self.refusal is not None:
            raise self.refusal
        self.reserved.append((job_type, count))
        yield FakeReservation(self)
