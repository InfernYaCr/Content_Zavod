"""Стиль автора по образцам (#134): the bot half of the `analyze_style` Job.

«📄 Пришлю статью» / «📨 Посты канала» sit on top of the Стиль автора chooser, on the Экран
Настроек and in the onboarding wizard (`settings_screen.build_style_start_rows`). Pressing one
asks for Образцы with a `style_samples` wait (`PendingInputs`, #88) and «✅ Готово» under the
question; for posts the button says «✅ Это мой канал — готово», the explicit consent to read the
channel. Its id is `<mode>:<origin>` (`StyleRef`): articles or posts, and the screen it came from,
where «Отмена» and «🎨 Готовый стиль» go back to - the wizard's step, or the Экран Настроек.

While the wait is open, every message of the Владелец is an Образец, and the question is edited
to count what arrived: a text (Telegram cuts a long one into several messages, so they are
collected until «Готово» - the button, or the word typed), a forwarded post, a `.docx`/`.txt`/
`.md` file up to `MAX_FILE_BYTES`, or a link (`@канал`, `t.me/<канал>`, a site, VC.ru - normalized
like the Проект, #133; a short message with one `https://` link in it, as a phone's «Поделиться»
sends it, counts as the link; at most `MAX_LINKS`). A Дзен link is refused at once - Дзен answers
302 to a login page - and a Telegram link only counts for posts, where the consent is asked. Each
accepted message renews the wait. A `.docx` is read in a thread, `word/document.xml` only, after
a check of how big it unpacks.

Texts are kept in this process's memory only, per (Пространство, chat, user), and forgotten with
the wait's TTL; nothing of them is written to the database before «Готово». After a bot restart
the wait survives but the texts don't: the Владелец is asked to send them again.

«Готово» with less than `MIN_CHARS` of text and no link keeps collecting and says so, with
«🎨 Готовый стиль»; otherwise an `analyze_style` Job is enqueued with the Образцы (the model call
runs in the worker, ADR-0004) and only then the question turns into «⏳ Читаю ваши тексты…».
When it finishes, its notification edits that message into «Вот как вы пишете: …» with
[✅ Да, это я] [✏️ Поправить] [🔄 Другие образцы] - or into why there is nothing to confirm - and
strips the Образцы from the Job's payload: only the profile stays (in the Job's output, until
confirmed, then in the Настройки). The buttons carry the Job id and read the profile back from
the Job of the presser's own Пространство (ADR-0015), so nothing is saved until «✅» and a stale
button saves exactly what its message showed. «✏️ Поправить» asks for the Владелец's own words
and saves the profile with them as corrections the prompts rank above the profile; its result
message loses its buttons, and a later «✅» on the same profile keeps the corrections.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
import zipfile
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field, replace
from io import BytesIO
from typing import Any, Protocol
from urllib.parse import urlsplit
from xml.etree.ElementTree import iterparse

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from ..job_queue import JobId, JobResult, JobSnapshot
from ..settings import (
    SettingsService,
    StyleProfile,
    normalize_project_url,
    style_profile_from_dict,
    style_profile_text,
)
from ..settings.style_profile import CORRECTIONS_MAX_LENGTH
from ..workspaces import LimitRefused, Workspace, WorkspaceId
from .callback_codec import Action, SimpleAction, encode_callback_data
from .comment_gated_regeneration import PendingInputStore
from .gateway import BotClient, MessageGone
from .input_prompt import InputPrompt, build_cancel_input_keyboard
from .onboarding import ONBOARDING_ORDER, REVIEW, StepRef
from .pending_inputs import PENDING_INPUT_TTL, PendingInput
from .settings_screen import STYLE_ARTICLES, STYLE_FROM_SETTINGS, STYLE_FROM_WIZARD, STYLE_POSTS
from .texts import (
    MENU_SETTINGS_BUTTON,
    STALE_BUTTON,
    STYLE_AGAIN_BUTTON,
    STYLE_ARTICLES_QUESTION,
    STYLE_CONFIRM_HINT,
    STYLE_CONTINUE_BUTTON,
    STYLE_CORRECTIONS_LINE,
    STYLE_DONE_BUTTON,
    STYLE_DONE_WORDS,
    STYLE_ENOUGH,
    STYLE_FAILED,
    STYLE_FIX_BUTTON,
    STYLE_FIX_INVALID,
    STYLE_FIX_QUESTION,
    STYLE_LOW_CONFIDENCE,
    STYLE_NOTHING_YET,
    STYLE_OK_BUTTON,
    STYLE_POSTS_DONE_BUTTON,
    STYLE_POSTS_QUESTION,
    STYLE_PRESETS_BUTTON,
    STYLE_RESTARTED,
    STYLE_RESULT_TITLE,
    STYLE_RESULT_TOO_SHORT,
    STYLE_SAVED,
    STYLE_TALLY,
    STYLE_TALLY_LINKS,
    STYLE_TOO_SHORT,
    STYLE_UNREAD,
    STYLE_WARN_DZEN,
    STYLE_WARN_FILE_BIG,
    STYLE_WARN_FILE_SIZE_UNKNOWN,
    STYLE_WARN_FILE_TYPE,
    STYLE_WARN_FILE_UNREADABLE,
    STYLE_WARN_LINKS,
    STYLE_WARN_TELEGRAM_LINK,
    STYLE_WARN_UNSUPPORTED,
    STYLE_WORKING,
    TO_MENU_BUTTON,
    error_alert_text,
)

logger = logging.getLogger(__name__)

ANALYZE_STYLE_JOB = "analyze_style"
"""Same string as `pipelines.ANALYZE_STYLE_JOB` - bot and worker share only the queue."""
STYLE_SAMPLES_KIND = "style_samples"
STYLE_FIX_KIND = "style_fix"

MIN_CHARS = 1500
MAX_CHARS = 12_000
MAX_LINKS = 3
MAX_FILE_BYTES = 1_000_000
# A .docx is a zip: how big its text part may unpack before it is parsed at all.
DOCX_MAX_UNPACKED = 3_000_000
_DOCX_MAX_PARAGRAPHS = 5_000
_DOCX_TEXT = "word/document.xml"
_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_FILE_TYPES = (".docx", ".txt", ".md")
_DZEN_HOSTS = ("dzen.ru", "zen.yandex.ru")
_TELEGRAM_HOSTS = ("t.me", "telegram.me")
_RAW_PAYLOAD_KEYS = ["samples", "links"]
# «Заголовок\nhttps://vc.ru/…» - what a phone's «Поделиться» sends - is still one link.
_SHARED_LINK_MAX_CHARS = 300
_HTTP_URL_RE = re.compile(r"https?://\S+", re.I)
_TTL_SECONDS = PENDING_INPUT_TTL.total_seconds()

Download = Callable[[str], Awaitable[bytes]]


@dataclass(frozen=True)
class SampleDocument:
    file_id: str
    file_name: str
    file_size: int | None


@dataclass(frozen=True)
class IncomingSample:
    """A private-chat message, unpacked to what can be an Образец: its text or caption, and a
    document if any."""

    text: str | None
    document: SampleDocument | None = None


@dataclass(frozen=True)
class StyleRef:
    """`<mode>:<origin>`, in `style_start` ids, the wait's target and the Job's payload. A bare
    mode (a button from before origins) came from the Экран Настроек."""

    mode: str
    origin: str = STYLE_FROM_SETTINGS

    def encode(self) -> str:
        return f"{self.mode}:{self.origin}"

    @classmethod
    def decode(cls, text: str) -> StyleRef:
        mode, _, origin = text.partition(":")
        return cls(
            STYLE_POSTS if mode == STYLE_POSTS else STYLE_ARTICLES,
            STYLE_FROM_WIZARD if origin == STYLE_FROM_WIZARD else STYLE_FROM_SETTINGS,
        )


class StyleJobs(Protocol):
    async def enqueue(
        self,
        workspace_id: WorkspaceId,
        job_type: str,
        payload: dict[str, Any],
        idempotency_key: str,
        *,
        conn: Any = None,
    ) -> JobId: ...

    async def get_job(self, workspace_id: WorkspaceId, job_id: JobId) -> JobSnapshot | None: ...

    async def drop_payload_keys(
        self, workspace_id: WorkspaceId, job_id: JobId, keys: list[str]
    ) -> None: ...


class _Reservation(Protocol):
    conn: Any


class StyleLimits(Protocol):
    """`Limits.reserve` (ADR-0015): `analyze_style` is one unfinished Job at a time and
    counts toward the daily cap of free Jobs; it costs no trial Лимит."""

    def reserve(
        self, workspace_id: WorkspaceId, job_type: str | None = None, count: int = 1
    ) -> AbstractAsyncContextManager[_Reservation]: ...


class OnboardingNeed(Protocol):
    async def needed(self, workspace_id: WorkspaceId) -> bool: ...


@dataclass
class _Collected:
    # ponytail: process memory - a bot restart drops an unfinished collection (the Владелец
    # is asked to send again); keeps the Образцы out of the database until «Готово».
    texts: list[str] = field(default_factory=list)
    links: list[str] = field(default_factory=list)
    touched: float = field(default_factory=time.monotonic)

    @property
    def chars(self) -> int:
        return sum(map(len, self.texts))


@dataclass(frozen=True)
class _Refusal:
    warning: str


def _button(text: str, action: Action, id_: str = "") -> InlineKeyboardButton:
    return InlineKeyboardButton(
        text=text, callback_data=encode_callback_data(SimpleAction(action, id_))
    )


def _normalized_link(text: str) -> str | None:
    try:
        return normalize_project_url(text)
    except ValueError:
        return None


def sample_link(text: str) -> str | None:
    """A message that is one link - alone, or with a short title around one `https://` link as
    a phone shares it - normalized; `None` for prose."""
    text = text.strip()
    if not any(char.isspace() for char in text):
        return _normalized_link(text)
    urls = _HTTP_URL_RE.findall(text)
    if len(text) <= _SHARED_LINK_MAX_CHARS and len(urls) == 1:
        return _normalized_link(urls[0])
    return None


def _host_in(url: str, hosts: tuple[str, ...]) -> bool:
    host = urlsplit(url).hostname or ""
    return any(host == name or host.endswith(f".{name}") for name in hosts)


def _docx_text(data: bytes) -> str | None:
    """`word/document.xml`'s paragraphs - nothing else of the package is unpacked or parsed."""
    with zipfile.ZipFile(BytesIO(data)) as archive:
        info = archive.getinfo(_DOCX_TEXT)
        if info.file_size > DOCX_MAX_UNPACKED:
            return None
        paragraphs: list[str] = []
        size = 0
        with archive.open(info) as xml:
            for _event, element in iterparse(xml):
                if element.tag != f"{_W}p":
                    continue
                text = "".join(node.text or "" for node in element.iter(f"{_W}t"))
                element.clear()
                paragraphs.append(text)
                size += len(text)
                if len(paragraphs) >= _DOCX_MAX_PARAGRAPHS or size >= MAX_CHARS:
                    break
    return "\n".join(paragraphs).strip() or None


def document_text(data: bytes, file_name: str) -> str | None:
    """The plain text of a `.docx`, `.txt` or `.md` file; `None` when it can't be read."""
    if file_name.lower().endswith(".docx"):
        try:
            return _docx_text(data)
        except Exception:  # an untrusted file: any parse error just means "unreadable"
            logger.info("style sample: unreadable .docx", exc_info=True)
            return None
    encodings = ("utf-16",) if data[:2] in (b"\xff\xfe", b"\xfe\xff") else ("utf-8-sig", "cp1251")
    for encoding in encodings:
        try:
            return data.decode(encoding).strip() or None
        except UnicodeDecodeError:
            continue
    return None


def _question(ref: StyleRef) -> str:
    return STYLE_POSTS_QUESTION if ref.mode == STYLE_POSTS else STYLE_ARTICLES_QUESTION


def _collect_keyboard(ref: StyleRef, *, presets: bool = False) -> InlineKeyboardMarkup:
    done = STYLE_POSTS_DONE_BUTTON if ref.mode == STYLE_POSTS else STYLE_DONE_BUTTON
    rows = [[_button(done, "style_done", ref.encode())]]
    if presets:
        rows.append([_button(STYLE_PRESETS_BUTTON, "style_presets", ref.origin)])
    return build_cancel_input_keyboard(STYLE_SAMPLES_KIND, rows)


def _tally(collected: _Collected) -> str:
    text = STYLE_TALLY.format(chars=collected.chars)
    if collected.links:
        text += STYLE_TALLY_LINKS.format(links=len(collected.links))
    if collected.chars >= MAX_CHARS:
        text += f"\n{STYLE_ENOUGH}"
    return text


def _retry_keyboard(ref: StyleRef) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_button(STYLE_AGAIN_BUTTON, "style_start", ref.encode())],
            [_button(STYLE_PRESETS_BUTTON, "style_presets", ref.origin)],
        ]
    )


def render_profile(profile: StyleProfile) -> str:
    """The profile as the Владелец reads it: the summary on «вы», then the traits."""
    blocks = [profile.summary, style_profile_text(profile)]
    if profile.corrections:
        blocks.append(STYLE_CORRECTIONS_LINE.format(corrections=profile.corrections))
    return "\n\n".join(block for block in blocks if block)


def render_result(
    job_id: JobId, ref: StyleRef, output: dict[str, Any] | None
) -> tuple[str, InlineKeyboardMarkup]:
    """A finished `analyze_style` Job's message: the profile to confirm, or why there is none."""
    if output is None:
        return STYLE_FAILED, _retry_keyboard(ref)
    unread = [str(link) for link in output.get("unread") or []]
    unread_line = STYLE_UNREAD.format(links=", ".join(unread)) if unread else ""
    profile = style_profile_from_dict(output.get("profile"))
    if output.get("status") != "ok" or profile is None:
        chars = int(output.get("chars") or 0)
        text = STYLE_RESULT_TOO_SHORT.format(chars=chars, minimum=MIN_CHARS)
        return "\n\n".join(filter(None, [text, unread_line])), _retry_keyboard(ref)
    blocks = [STYLE_RESULT_TITLE, render_profile(profile)]
    if profile.confidence == "low":
        blocks.append(STYLE_LOW_CONFIDENCE)
    blocks += [unread_line, STYLE_CONFIRM_HINT]
    job = str(job_id)
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [_button(STYLE_OK_BUTTON, "style_ok", job)],
            [_button(STYLE_FIX_BUTTON, "style_fix", job)],
            [_button(STYLE_AGAIN_BUTTON, "style_start", ref.encode())],
        ]
    )
    return "\n\n".join(filter(None, blocks)), keyboard


def _same_profile(a: StyleProfile, b: StyleProfile) -> bool:
    return replace(a, corrections="") == replace(b, corrections="")


class StyleSamples:
    def __init__(
        self,
        jobs: StyleJobs,
        settings: SettingsService,
        pending: PendingInputStore,
        prompts: InputPrompt,
        bot: BotClient,
        download: Download,
        onboarding: OnboardingNeed,
        limits: StyleLimits,
    ) -> None:
        self._limits = limits
        self._jobs = jobs
        self._settings = settings
        self._pending = pending
        self._prompts = prompts
        self._bot = bot
        self._download = download
        self._onboarding = onboarding
        self._collected: dict[tuple[WorkspaceId, int, int], _Collected] = {}

    # --- collecting ---

    async def start(self, workspace: Workspace, chat_id: int, user_id: int, ref_text: str) -> None:
        """«📄 Пришлю статью» / «📨 Посты канала» / «🔄 Другие образцы»: ask for Образцы."""
        self._sweep()
        ref = StyleRef.decode(ref_text)
        message_id = await self._bot.send_message(
            chat_id, _question(ref), reply_markup=_collect_keyboard(ref)
        )
        self._collected[(workspace.id, chat_id, user_id)] = _Collected()
        replaced = await self._pending.put(
            workspace.id,
            chat_id,
            user_id,
            PendingInput(STYLE_SAMPLES_KIND, ref.encode(), message_id),
        )
        if replaced is not None:
            await self._prompts.close(chat_id, replaced)

    async def handle_message(
        self, workspace: Workspace, chat_id: int, user_id: int, sample: IncomingSample
    ) -> bool:
        """A message while Образцы are being collected; `False` if none are."""
        if (sample.text or "").startswith("/"):
            return False
        pending = await self._pending.get(workspace.id, chat_id, user_id)
        if pending is None or pending.kind != STYLE_SAMPLES_KIND:
            return False
        if sample.document is None and _is_done_word(sample.text or ""):
            alert = await self.done(workspace, chat_id, user_id, pending.prompt_message_id)
            if alert:
                await self._bot.send_message(chat_id, alert)
            return True
        self._sweep()
        ref = StyleRef.decode(pending.target_id)
        key = (workspace.id, chat_id, user_id)
        collected = self._collected.get(key)
        restarted = collected is None
        if collected is None:
            collected = self._collected[key] = _Collected()
        warning = await self._accept(collected, ref, sample)
        if restarted:  # the wait outlived a bot restart, what came before it didn't
            warning = STYLE_RESTARTED
        collected.touched = time.monotonic()
        # The Владелец is still at it: the wait lives on from now, not from «Пришлю статью».
        await self._pending.put(workspace.id, chat_id, user_id, pending)
        text = f"{_question(ref)}\n\n{_tally(collected)}"
        if warning:
            text += f"\n\n{warning}"
        try:
            await self._bot.edit_message_text(
                chat_id, pending.prompt_message_id, text, reply_markup=_collect_keyboard(ref)
            )
        except MessageGone:
            logger.info("style samples: the question in chat %s is gone", chat_id)
        return True

    async def done(
        self, workspace: Workspace, chat_id: int, user_id: int, message_id: int
    ) -> str | None:
        """«✅ Готово»: start the analysis, or keep collecting. Returns an alert for the button,
        if any."""
        key = (workspace.id, chat_id, user_id)
        pending = await self._pending.take(
            workspace.id, chat_id, user_id, STYLE_SAMPLES_KIND, reply_to_message_id=message_id
        )
        if pending is None:  # expired, cancelled or already sent
            self._collected.pop(key, None)
            return STALE_BUTTON
        ref = StyleRef.decode(pending.target_id)
        collected = self._collected.get(key)
        if collected is None:  # the bot restarted since «Пришлю статью»
            await self._pending.put(workspace.id, chat_id, user_id, pending)
            self._collected[key] = _Collected()
            return STYLE_RESTARTED
        if not collected.texts and not collected.links:
            await self._pending.put(workspace.id, chat_id, user_id, pending)
            return STYLE_NOTHING_YET
        if not collected.links and collected.chars < MIN_CHARS:
            await self._pending.put(workspace.id, chat_id, user_id, pending)
            text = STYLE_TOO_SHORT.format(chars=collected.chars, minimum=MIN_CHARS)
            await self._bot.edit_message_text(
                chat_id,
                message_id,
                f"{_question(ref)}\n\n{text}",
                reply_markup=_collect_keyboard(ref, presets=True),
            )
            return None
        payload = {
            "samples": collected.texts,
            "links": collected.links,
            "source": "posts" if ref.mode == STYLE_POSTS else "articles",
            "mode": ref.encode(),
            "message_id": message_id,
        }
        try:
            async with self._limits.reserve(workspace.id, ANALYZE_STYLE_JOB) as reservation:
                await self._jobs.enqueue(
                    workspace.id,
                    ANALYZE_STYLE_JOB,
                    payload,
                    f"{ANALYZE_STYLE_JOB}:{chat_id}:{message_id}",
                    conn=reservation.conn,
                )
        except LimitRefused as exc:
            # «Дождитесь…» / the daily cap: the Образцы and the wait stay for later.
            await self._pending.put(workspace.id, chat_id, user_id, pending)
            return error_alert_text(exc)
        except Exception:
            # Nothing is lost: the Образцы and the wait stay for another «Готово».
            await self._pending.put(workspace.id, chat_id, user_id, pending)
            raise
        self._collected.pop(key, None)
        await self._bot.edit_message_text(chat_id, message_id, STYLE_WORKING)
        return None

    async def cancel(
        self, workspace: Workspace, chat_id: int, user_id: int, message_id: int
    ) -> str | None:
        """«Отмена» on the question: stop collecting; the origin to go back to, or `None` for a
        stale button. The question itself is left to the caller."""
        pending = await self._pending.take(
            workspace.id, chat_id, user_id, STYLE_SAMPLES_KIND, reply_to_message_id=message_id
        )
        self._collected.pop((workspace.id, chat_id, user_id), None)
        return None if pending is None else StyleRef.decode(pending.target_id).origin

    async def drop(self, workspace: Workspace, chat_id: int, user_id: int) -> None:
        """«🎨 Готовый стиль»: stop collecting and forget what arrived. The message is left to the
        caller (it becomes the chooser)."""
        self._collected.pop((workspace.id, chat_id, user_id), None)
        await self._pending.take(workspace.id, chat_id, user_id, STYLE_SAMPLES_KIND)

    def _sweep(self) -> None:
        """Forget collections whose wait has expired - the Образцы don't outlive it."""
        now = time.monotonic()
        for key in [k for k, c in self._collected.items() if now - c.touched > _TTL_SECONDS]:
            del self._collected[key]

    async def _accept(
        self, collected: _Collected, ref: StyleRef, sample: IncomingSample
    ) -> str | None:
        """Add one message to `collected`; a warning for the Владелец if it isn't usable."""
        if sample.document is not None:
            text = await self._document(sample.document)
            if isinstance(text, _Refusal):
                return text.warning
            self._add_text(collected, text)
            return None
        text = (sample.text or "").strip()
        if not text:
            return STYLE_WARN_UNSUPPORTED
        link = sample_link(text)
        if link is None:
            self._add_text(collected, text)
            return None
        if _host_in(link, _DZEN_HOSTS):
            return STYLE_WARN_DZEN
        if ref.mode != STYLE_POSTS and _host_in(link, _TELEGRAM_HOSTS):
            return STYLE_WARN_TELEGRAM_LINK
        if len(collected.links) >= MAX_LINKS:
            return STYLE_WARN_LINKS
        if link not in collected.links:
            collected.links.append(link)
        return None

    @staticmethod
    def _add_text(collected: _Collected, text: str) -> None:
        room = MAX_CHARS - collected.chars
        if room > 0:
            collected.texts.append(text[:room])

    async def _document(self, document: SampleDocument) -> str | _Refusal:
        if not document.file_name.lower().endswith(_FILE_TYPES):
            return _Refusal(STYLE_WARN_FILE_TYPE)
        if document.file_size is None:
            return _Refusal(STYLE_WARN_FILE_SIZE_UNKNOWN)
        if document.file_size > MAX_FILE_BYTES:
            return _Refusal(STYLE_WARN_FILE_BIG)
        data = await self._download(document.file_id)
        if len(data) > MAX_FILE_BYTES:
            return _Refusal(STYLE_WARN_FILE_BIG)
        # Off the event loop: one Владелец's file never stalls the bot for everybody else.
        text = await asyncio.to_thread(document_text, data, document.file_name)
        return text if text else _Refusal(STYLE_WARN_FILE_UNREADABLE)

    # --- the result ---

    async def forget_samples(self, result: JobResult) -> None:
        """The DB half of a finished `analyze_style` Job: its Образцы leave the payload."""
        await self._jobs.drop_payload_keys(
            result.workspace_id, JobId(result.job_id), _RAW_PAYLOAD_KEYS
        )

    async def deliver(self, chat_id: int, result: JobResult) -> None:
        """Its «⏳» message becomes the profile to confirm - or why there is none."""
        job = await self._jobs.get_job(result.workspace_id, JobId(result.job_id))
        if job is None:
            return
        output = result.output if result.status == "done" else None
        ref = StyleRef.decode(str(job.payload.get("mode") or ""))
        text, keyboard = render_result(job.job_id, ref, output)
        message_id = job.payload.get("message_id")
        if isinstance(message_id, int):
            try:
                await self._bot.edit_message_text(chat_id, message_id, text, reply_markup=keyboard)
                return
            except MessageGone:
                pass
        await self._bot.send_message(chat_id, text, reply_markup=keyboard)

    # --- the buttons ---

    async def confirm(
        self, workspace: Workspace, chat_id: int, message_id: int, job_text: str
    ) -> bool:
        """«✅ Да, это я»: save that very Job's profile; `False` if it has none. Corrections
        already saved for this same profile («✏️ Поправить») are kept."""
        profile = await self._profile(workspace.id, job_text)
        if profile is None:
            return False
        stored = (await self._settings.read(workspace.id)).style_profile
        if stored is not None and stored.corrections and _same_profile(stored, profile):
            profile = stored
        await self._settings.set_style_profile(workspace.id, profile)
        await self._bot.edit_message_text(
            chat_id,
            message_id,
            STYLE_SAVED.format(summary=render_profile(profile)),
            reply_markup=await self._next_keyboard(workspace),
        )
        return True

    async def ask_fix(
        self, workspace: Workspace, chat_id: int, user_id: int, message_id: int, job_text: str
    ) -> bool:
        """«✏️ Поправить»: ask for the Владелец's own words about that Job's profile. The result
        message loses its buttons, so its «✅» can't save the profile without them later."""
        if await self._profile(workspace.id, job_text) is None:
            return False
        await self._bot.edit_message_reply_markup(chat_id, message_id, None)
        await self._prompts.ask(
            workspace.id, chat_id, user_id, STYLE_FIX_KIND, job_text, STYLE_FIX_QUESTION
        )
        return True

    async def handle_reply(
        self, workspace: Workspace, chat_id: int, user_id: int, text: str
    ) -> bool:
        """The answer to «✏️ Поправить»; `False` if nothing asked for one."""
        pending = await self._prompts.take(workspace.id, chat_id, user_id, STYLE_FIX_KIND)
        if pending is None:
            return False
        profile = await self._profile(workspace.id, pending.target_id)
        if profile is None:  # the Job is gone: nothing to correct
            await self._prompts.close(chat_id, pending)
            return True
        corrections = text.strip()
        if not corrections or len(corrections) > CORRECTIONS_MAX_LENGTH:
            await self._prompts.ask_again(
                workspace.id,
                chat_id,
                user_id,
                pending,
                STYLE_FIX_INVALID.format(limit=CORRECTIONS_MAX_LENGTH),
                STYLE_FIX_QUESTION,
            )
            return True
        profile = replace(profile, corrections=corrections)
        await self._settings.set_style_profile(workspace.id, profile)
        await self._prompts.close(chat_id, pending)
        await self._bot.send_message(
            chat_id,
            STYLE_SAVED.format(summary=render_profile(profile)),
            reply_markup=await self._next_keyboard(workspace),
        )
        return True

    # --- helpers ---

    async def _profile(self, workspace_id: WorkspaceId, job_text: str) -> StyleProfile | None:
        """The profile of the `analyze_style` Job a button names, in this Пространство only."""
        if not job_text.isdigit():
            return None
        job = await self._jobs.get_job(workspace_id, JobId(int(job_text)))
        if job is None or job.job_type != ANALYZE_STYLE_JOB or job.status != "done":
            return None
        output = job.output or {}
        if output.get("status") != "ok":
            return None
        return style_profile_from_dict(output.get("profile"))

    async def _next_keyboard(self, workspace: Workspace) -> InlineKeyboardMarkup:
        """Back into the wizard while it is unfinished, else to Настройки or the menu."""
        if await self._onboarding.needed(workspace.id):
            index = ONBOARDING_ORDER.index("persona") + 1
            step = (
                StepRef(ONBOARDING_ORDER[index]).encode()
                if index < len(ONBOARDING_ORDER)
                else REVIEW
            )
            return InlineKeyboardMarkup(
                inline_keyboard=[[_button(STYLE_CONTINUE_BUTTON, "onboarding_step", step)]]
            )
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [_button(MENU_SETTINGS_BUTTON, "settings")],
                [_button(TO_MENU_BUTTON, "menu")],
            ]
        )


def _is_done_word(text: str) -> bool:
    return text.strip().strip(".!").lower() in STYLE_DONE_WORDS
