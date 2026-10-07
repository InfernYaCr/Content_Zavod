"""Направления suggested from the Ниша (#113): the bot half of the `suggest_directions` Job.

«✨ Предложить по Нише» sits on the Направления question - on the Экран Настроек and in the
onboarding wizard. Pressing it drops that question's wait, turns the question message itself
into «⏳ Подбираю…» and enqueues a `suggest_directions` Job; the model call and the Wordstat check run in the worker
like every other LLM call (ADR-0004, provenance #74), never in the bot process. When the Job
finishes, its notification edits the «⏳» message into the list, with
[✅ Взять] [🔄 Ещё варианты] / [✏️ Написать свои] [Отмена].

Nothing is saved until «✅ Взять». The list itself is never packed into callback data: «Взять»
and «Ещё варианты» carry the Job id and read the list back from the Job's stored output, so a
button always means exactly the list on its own message - a stale one included. «Ещё
варианты» edits the same message back to «⏳» and enqueues a new Job told to avoid every query
already shown. «✏️ Написать свои» and «Отмена» carry the origin instead, so they work on the
«⏳» message too. A result whose message is gone by then (the Владелец pressed «Отмена» while
it was being picked) is dropped silently - a result never posts a new message.

Where a suggestion goes back to is its origin, `<prefix>:<place>`: `o:<StepRef>` for the wizard,
`s:<Экран Настроек message id>` for the Экран Настроек. Each prefix is a `DirectionsOrigin`
attached at start-up (the wizard and the screen also start suggestions, hence attach rather
than a constructor argument).
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any, Protocol

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from ..job_queue import JobId, JobResult, JobSnapshot
from ..settings import SettingsService
from .callback_codec import Action, SimpleAction, encode_callback_data
from .gateway import BotClient, MessageGone
from .settings_screen import SETTING_FIELDS
from .texts import (
    CANCEL_BUTTON,
    DIRECTIONS_DROPPED,
    DIRECTIONS_FAILED,
    DIRECTIONS_MORE_BUTTON,
    DIRECTIONS_NICHE_CHANGED,
    DIRECTIONS_NONE_FOUND,
    DIRECTIONS_OWN_BUTTON,
    DIRECTIONS_RETRY_BUTTON,
    DIRECTIONS_SUGGESTED_HINT,
    DIRECTIONS_SUGGESTED_LINE,
    DIRECTIONS_SUGGESTED_LINE_UNCHECKED,
    DIRECTIONS_SUGGESTED_TITLE,
    DIRECTIONS_TAKE_BUTTON,
    DIRECTIONS_WORDSTAT_UNAVAILABLE,
    DIRECTIONS_WORKING,
    frequency_text,
)

logger = logging.getLogger(__name__)

SUGGEST_DIRECTIONS_JOB = "suggest_directions"
"""Same string as `pipelines.SUGGEST_DIRECTIONS_JOB` - the telegram layer doesn't import the
pipelines (bot and worker share only the queue)."""

ONBOARDING_ORIGIN = "o"
SETTINGS_ORIGIN = "s"

# «Ещё варианты» tells the model what not to repeat; the oldest shown fall off past this.
MAX_EXCLUDED = 40

_DIRECTIONS_FIELD = next(setting for setting in SETTING_FIELDS if setting.key == "directions")


class DirectionsOrigin(Protocol):
    """A screen a suggestion is asked from, and goes back to (`place` is its part of the
    origin id)."""

    async def suggestion_opened(
        self, chat_id: int, user_id: int, message_id: int, place: str
    ) -> None:
        """«✨ Предложить по Нише» was pressed on `message_id`: drop that question's wait (the
        message itself becomes «⏳»)."""
        ...

    async def suggestion_taken(self, chat_id: int, user_id: int, place: str, notice: str) -> None:
        """«✅ Взять» saved the Направления: show where to go next, with `notice`."""
        ...

    async def suggestion_declined(
        self, chat_id: int, user_id: int, place: str, *, write_own: bool
    ) -> None:
        """«✏️ Написать свои» (`write_own`) or «Отмена»: back to typing them, or just back."""
        ...


class SuggestionJobs(Protocol):
    async def enqueue(
        self, job_type: str, payload: dict[str, Any], idempotency_key: str
    ) -> JobId: ...

    async def get_job(self, job_id: JobId) -> JobSnapshot | None: ...


def _button(text: str, action: Action, id_: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(
        text=text, callback_data=encode_callback_data(SimpleAction(action, id_))
    )


def _keyboard(rows: list[list[InlineKeyboardButton]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _declines(origin: str) -> list[InlineKeyboardButton]:
    return [
        _button(DIRECTIONS_OWN_BUTTON, "directions_own", origin),
        _button(CANCEL_BUTTON, "directions_cancel", origin),
    ]


def render_suggestion(
    job_id: JobId, origin: str, output: dict[str, Any] | None
) -> tuple[str, InlineKeyboardMarkup]:
    """The finished Job's message: the list with «Взять», or why there is nothing to take."""
    job = str(job_id)
    queries = (output or {}).get("queries") or []
    if output is None:
        return DIRECTIONS_FAILED, _keyboard(
            [[_button(DIRECTIONS_RETRY_BUTTON, "directions_more", job)], _declines(origin)]
        )
    niche = output.get("niche", "")
    dropped = output.get("dropped") or []
    if not queries:
        blocks = [DIRECTIONS_NONE_FOUND.format(niche=niche)]
        if dropped:
            blocks.append(DIRECTIONS_DROPPED.format(queries=", ".join(dropped)))
        return "\n\n".join(blocks), _keyboard(
            [[_button(DIRECTIONS_MORE_BUTTON, "directions_more", job)], _declines(origin)]
        )
    lines = [
        DIRECTIONS_SUGGESTED_LINE.format(
            query=item["query"], frequency=frequency_text(item["frequency"])
        )
        if item.get("frequency")
        else DIRECTIONS_SUGGESTED_LINE_UNCHECKED.format(query=item["query"])
        for item in queries
    ]
    blocks = [DIRECTIONS_SUGGESTED_TITLE.format(niche=niche), "\n".join(lines)]
    if output.get("wordstat") == "unavailable":
        blocks.append(DIRECTIONS_WORDSTAT_UNAVAILABLE)
    elif dropped:
        blocks.append(DIRECTIONS_DROPPED.format(queries=", ".join(dropped)))
    blocks.append(DIRECTIONS_SUGGESTED_HINT)
    return "\n\n".join(blocks), _keyboard(
        [
            [
                _button(DIRECTIONS_TAKE_BUTTON, "directions_take", job),
                _button(DIRECTIONS_MORE_BUTTON, "directions_more", job),
            ],
            _declines(origin),
        ]
    )


class DirectionSuggestions:
    def __init__(self, jobs: SuggestionJobs, settings: SettingsService, bot: BotClient) -> None:
        self._jobs = jobs
        self._settings = settings
        self._bot = bot
        self._origins: dict[str, DirectionsOrigin] = {}

    def attach(self, prefix: str, origin: DirectionsOrigin) -> None:
        self._origins[prefix] = origin

    # --- asking ---

    async def start(self, chat_id: int, user_id: int, message_id: int, origin: str) -> None:
        """«✨ Предложить по Нише» pressed on `message_id`: that question turns into «⏳» in
        place, so a double tap edits the same message and enqueues the same Job."""
        found = self._origin(origin)
        if found is not None:
            screen, place = found
            await screen.suggestion_opened(chat_id, user_id, message_id, place)
        await self.request(chat_id, origin, message_id=message_id)

    async def request(
        self,
        chat_id: int,
        origin: str,
        *,
        message_id: int | None = None,
        exclude: Sequence[str] = (),
        previous: JobId | None = None,
        niche_changed: bool = False,
    ) -> None:
        """Show «⏳» - as a new message, or in place of `message_id` - and enqueue the Job.
        `niche_changed`: started by itself after a Ниша change, so say why first."""
        niche = (await self._settings.read()).niche
        text = DIRECTIONS_WORKING.format(niche=niche)
        if niche_changed:
            text = f"{DIRECTIONS_NICHE_CHANGED}\n\n{text}"
        keyboard = _keyboard([[_button(CANCEL_BUTTON, "directions_cancel", origin)]])
        if message_id is None:
            message_id = await self._bot.send_message(chat_id, text, reply_markup=keyboard)
        else:
            try:
                await self._bot.edit_message_text(chat_id, message_id, text, reply_markup=keyboard)
            except MessageGone:
                return  # deleted under the tap: nowhere to show the result, so no Job
        # One Job per «⏳» message and round: a double-tapped «Ещё варианты» enqueues once.
        await self._jobs.enqueue(
            SUGGEST_DIRECTIONS_JOB,
            {
                "chat_id": chat_id,
                "message_id": message_id,
                "origin": origin,
                "exclude": list(exclude),
            },
            idempotency_key=f"{SUGGEST_DIRECTIONS_JOB}:{chat_id}:{message_id}:{previous or 0}",
        )

    # --- the result ---

    async def deliver(self, result: JobResult) -> None:
        """A finished `suggest_directions` Job: its «⏳» message becomes the list."""
        job = await self._jobs.get_job(result.job_id)
        if job is None:
            return
        payload = job.payload
        output = result.output if result.status == "done" else None
        text, keyboard = render_suggestion(job.job_id, str(payload.get("origin", "")), output)
        try:
            await self._bot.edit_message_text(
                int(payload["chat_id"]), int(payload["message_id"]), text, reply_markup=keyboard
            )
        except MessageGone:
            # «Отмена» was pressed while it was being picked: nobody is waiting for it.
            logger.info("Направления suggestion %s: its message is gone", result.job_id)

    # --- the buttons ---

    async def take(self, chat_id: int, user_id: int, message_id: int, job_text: str) -> bool:
        """«✅ Взять»: save the list of that very Job; `False` if there is none to take."""
        job = await self._job(chat_id, job_text)
        queries = (
            [item["query"] for item in ((job.output or {}).get("queries") or [])] if job else []
        )
        if job is None or job.status != "done" or not queries:
            return False
        if (job.output or {}).get("niche") != (await self._settings.read()).niche:
            return False  # picked for a Ниша that has changed since
        notice = await _DIRECTIONS_FIELD.save(self._settings, ", ".join(queries))
        await self._bot.delete_message(chat_id, message_id)
        found = self._origin(str(job.payload.get("origin", "")))
        if found is not None:
            screen, place = found
            await screen.suggestion_taken(chat_id, user_id, place, notice)
        return True

    async def more(self, chat_id: int, user_id: int, message_id: int, job_text: str) -> bool:
        """«🔄 Ещё варианты» / «🔄 Попробовать ещё»: a new round avoiding what was shown."""
        job = await self._job(chat_id, job_text)
        if job is None:
            return False
        output = job.output or {}
        shown = [
            *(job.payload.get("exclude") or []),
            *(item["query"] for item in output.get("queries") or []),
            *(output.get("dropped") or []),
        ]
        exclude = list(dict.fromkeys(str(query) for query in shown))[-MAX_EXCLUDED:]
        await self.request(
            chat_id,
            str(job.payload.get("origin", "")),
            message_id=message_id,
            exclude=exclude,
            previous=job.job_id,
        )
        return True

    async def own(self, chat_id: int, user_id: int, message_id: int, origin: str) -> None:
        await self._decline(chat_id, user_id, message_id, origin, write_own=True)

    async def cancel(self, chat_id: int, user_id: int, message_id: int, origin: str) -> None:
        await self._decline(chat_id, user_id, message_id, origin, write_own=False)

    # --- helpers ---

    async def _decline(
        self, chat_id: int, user_id: int, message_id: int, origin: str, *, write_own: bool
    ) -> None:
        await self._bot.delete_message(chat_id, message_id)
        found = self._origin(origin)
        if found is not None:
            screen, place = found
            await screen.suggestion_declined(chat_id, user_id, place, write_own=write_own)

    async def _job(self, chat_id: int, job_text: str) -> JobSnapshot | None:
        """The `suggest_directions` Job a button names, if it was asked in this chat."""
        if not job_text.isdigit():
            return None
        job = await self._jobs.get_job(JobId(int(job_text)))
        if (
            job is None
            or job.job_type != SUGGEST_DIRECTIONS_JOB
            or job.payload.get("chat_id") != chat_id
        ):
            return None
        return job

    def _origin(self, origin: str) -> tuple[DirectionsOrigin, str] | None:
        prefix, _, place = origin.partition(":")
        screen = self._origins.get(prefix)
        return None if screen is None else (screen, place)
