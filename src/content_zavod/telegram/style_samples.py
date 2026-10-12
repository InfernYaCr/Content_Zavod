"""Стиль автора по образцам (#134): the bot half of the `analyze_style` Job.

«📄 Пришлю статью» / «📨 Посты канала» sit in the Стиль автора chooser, on the Экран Настроек
and in the onboarding wizard (`settings_screen.build_style_start_rows`). Pressing one asks for
Образцы with a `style_samples` wait (`PendingInputs`, #88) and «✅ Готово» under the question; for
posts the button says «✅ Это мой канал — готово», the explicit consent to read the channel.

While the wait is open, every message of the Владелец is an Образец, and the question is edited
to count what arrived: a text (Telegram cuts a long one into several messages, so they are
collected until «Готово»), a forwarded post, a `.docx`/`.txt`/`.md` file up to
`MAX_FILE_BYTES`, or a link (`@канал`, `t.me/<канал>`, a site, VC.ru - normalized like the
Проект, #133; at most `MAX_LINKS`). A Дзен link is refused at once - Дзен answers 302 to a login
page. Texts are kept in this process's memory only, per (Пространство, chat, user), and capped
at `MAX_CHARS`; nothing of them is written to the database before «Готово».

«Готово» with less than `MIN_CHARS` of text and no link keeps collecting and says so, with
«🎨 Готовый стиль»; otherwise the question turns into «⏳ Читаю ваши тексты…» and an
`analyze_style` Job is enqueued with the Образцы (the model call runs in the worker, ADR-0004).
When it finishes, its notification edits that message into «Вот как вы пишете: …» with
[✅ Да, это я] [✏️ Поправить] [🔄 Другие образцы] - or into why there is nothing to confirm - and
strips the Образцы from the Job's payload: only the profile stays (in the Job's output, until
confirmed, then in the Настройки). The buttons carry the Job id and read the profile back from
the Job of the presser's own Пространство (ADR-0015), so nothing is saved until «✅» and a stale
button saves exactly what its message showed. «✏️ Поправить» asks for the Владелец's own words
and saves the profile with them as corrections the prompts rank above the profile.
"""

from __future__ import annotations

import logging
import zipfile
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from io import BytesIO
from typing import Any, Protocol
from urllib.parse import urlsplit

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from docx import Document

from ..job_queue import JobId, JobResult, JobSnapshot
from ..settings import (
    SettingsService,
    StyleProfile,
    normalize_project_url,
    style_profile_from_dict,
    style_profile_text,
)
from ..settings.style_profile import CORRECTIONS_MAX_LENGTH
from ..workspaces import Workspace, WorkspaceId
from .callback_codec import Action, SimpleAction, encode_callback_data
from .comment_gated_regeneration import PendingInputStore
from .gateway import BotClient, MessageGone
from .input_prompt import InputPrompt, build_cancel_input_keyboard
from .onboarding import ONBOARDING_ORDER, REVIEW, StepRef
from .pending_inputs import PendingInput
from .settings_screen import STYLE_ARTICLES, STYLE_POSTS
from .texts import (
    MENU_SETTINGS_BUTTON,
    STALE_BUTTON,
    STYLE_AGAIN_BUTTON,
    STYLE_ARTICLES_QUESTION,
    STYLE_CONFIRM_HINT,
    STYLE_CONTINUE_BUTTON,
    STYLE_CORRECTIONS_LINE,
    STYLE_DONE_BUTTON,
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
    STYLE_RESULT_TITLE,
    STYLE_RESULT_TOO_SHORT,
    STYLE_SAVED,
    STYLE_TALLY,
    STYLE_TALLY_LINKS,
    STYLE_TOO_SHORT,
    STYLE_UNREAD,
    STYLE_WARN_DZEN,
    STYLE_WARN_FILE_BIG,
    STYLE_WARN_FILE_TYPE,
    STYLE_WARN_FILE_UNREADABLE,
    STYLE_WARN_LINKS,
    STYLE_WARN_UNSUPPORTED,
    STYLE_WORKING,
    TO_MENU_BUTTON,
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
# A .docx is a zip: what it may unpack to before python-docx is let at it.
_DOCX_MAX_UNPACKED = 20_000_000
_FILE_TYPES = (".docx", ".txt", ".md")
_DZEN_HOSTS = ("dzen.ru", "zen.yandex.ru")
_RAW_PAYLOAD_KEYS = ["samples", "links"]

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


class StyleJobs(Protocol):
    async def enqueue(
        self,
        workspace_id: WorkspaceId,
        job_type: str,
        payload: dict[str, Any],
        idempotency_key: str,
    ) -> JobId: ...

    async def get_job(self, workspace_id: WorkspaceId, job_id: JobId) -> JobSnapshot | None: ...

    async def drop_payload_keys(
        self, workspace_id: WorkspaceId, job_id: JobId, keys: list[str]
    ) -> None: ...


class OnboardingNeed(Protocol):
    async def needed(self, workspace_id: WorkspaceId) -> bool: ...


@dataclass
class _Collected:
    # ponytail: process memory - a bot restart drops an unfinished collection (the Владелец
    # sends again); keeps the Образцы out of the database until «Готово».
    texts: list[str] = field(default_factory=list)
    links: list[str] = field(default_factory=list)

    @property
    def chars(self) -> int:
        return sum(map(len, self.texts))


def _button(text: str, action: Action, id_: str = "") -> InlineKeyboardButton:
    return InlineKeyboardButton(
        text=text, callback_data=encode_callback_data(SimpleAction(action, id_))
    )


def sample_link(text: str) -> str | None:
    """A message that is one link and nothing else, normalized; `None` for prose."""
    if any(char.isspace() for char in text.strip()):
        return None
    try:
        return normalize_project_url(text.strip())
    except ValueError:
        return None


def _is_dzen(url: str) -> bool:
    host = urlsplit(url).hostname or ""
    return any(host == name or host.endswith(f".{name}") for name in _DZEN_HOSTS)


def document_text(data: bytes, file_name: str) -> str | None:
    """The plain text of a `.docx`, `.txt` or `.md` file; `None` when it can't be read."""
    if file_name.lower().endswith(".docx"):
        try:
            with zipfile.ZipFile(BytesIO(data)) as archive:
                if sum(info.file_size for info in archive.infolist()) > _DOCX_MAX_UNPACKED:
                    return None
            paragraphs = Document(BytesIO(data)).paragraphs
        except Exception:  # an untrusted file: any parse error just means "unreadable"
            logger.info("style sample: unreadable .docx", exc_info=True)
            return None
        return "\n".join(p.text for p in paragraphs).strip() or None
    for encoding in ("utf-8-sig", "cp1251"):
        try:
            return data.decode(encoding).strip() or None
        except UnicodeDecodeError:
            continue
    return None


def _question(mode: str) -> str:
    return STYLE_POSTS_QUESTION if mode == STYLE_POSTS else STYLE_ARTICLES_QUESTION


def _collect_keyboard(mode: str, *, presets: bool = False) -> InlineKeyboardMarkup:
    done = STYLE_POSTS_DONE_BUTTON if mode == STYLE_POSTS else STYLE_DONE_BUTTON
    rows = [[_button(done, "style_done", mode)]]
    if presets:
        rows.append([_button(STYLE_PRESETS_BUTTON, "style_presets")])
    return build_cancel_input_keyboard(STYLE_SAMPLES_KIND, rows)


def _tally(collected: _Collected) -> str:
    text = STYLE_TALLY.format(chars=collected.chars)
    if collected.links:
        text += STYLE_TALLY_LINKS.format(links=len(collected.links))
    if collected.chars >= MAX_CHARS:
        text += f"\n{STYLE_ENOUGH}"
    return text


def _retry_keyboard(mode: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_button(STYLE_AGAIN_BUTTON, "style_start", mode)],
            [_button(STYLE_PRESETS_BUTTON, "style_presets")],
        ]
    )


def render_profile(profile: StyleProfile) -> str:
    """The profile as the Владелец reads it: the summary on «вы», then the traits."""
    blocks = [profile.summary, style_profile_text(profile)]
    if profile.corrections:
        blocks.append(STYLE_CORRECTIONS_LINE.format(corrections=profile.corrections))
    return "\n\n".join(block for block in blocks if block)


def render_result(
    job_id: JobId, mode: str, output: dict[str, Any] | None
) -> tuple[str, InlineKeyboardMarkup]:
    """A finished `analyze_style` Job's message: the profile to confirm, or why there is none."""
    if output is None:
        return STYLE_FAILED, _retry_keyboard(mode)
    unread = [str(link) for link in output.get("unread") or []]
    unread_line = STYLE_UNREAD.format(links=", ".join(unread)) if unread else ""
    profile = style_profile_from_dict(output.get("profile"))
    if output.get("status") != "ok" or profile is None:
        chars = int(output.get("chars") or 0)
        text = STYLE_RESULT_TOO_SHORT.format(chars=chars, minimum=MIN_CHARS)
        return "\n\n".join(filter(None, [text, unread_line])), _retry_keyboard(mode)
    blocks = [STYLE_RESULT_TITLE, render_profile(profile)]
    if profile.confidence == "low":
        blocks.append(STYLE_LOW_CONFIDENCE)
    blocks += [unread_line, STYLE_CONFIRM_HINT]
    job = str(job_id)
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [_button(STYLE_OK_BUTTON, "style_ok", job)],
            [_button(STYLE_FIX_BUTTON, "style_fix", job)],
            [_button(STYLE_AGAIN_BUTTON, "style_start", mode)],
        ]
    )
    return "\n\n".join(filter(None, blocks)), keyboard


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
    ) -> None:
        self._jobs = jobs
        self._settings = settings
        self._pending = pending
        self._prompts = prompts
        self._bot = bot
        self._download = download
        self._onboarding = onboarding
        self._collected: dict[tuple[WorkspaceId, int, int], _Collected] = {}

    # --- collecting ---

    async def start(self, workspace: Workspace, chat_id: int, user_id: int, mode: str) -> None:
        """«📄 Пришлю статью» / «📨 Посты канала» / «🔄 Другие образцы»: ask for Образцы."""
        mode = STYLE_POSTS if mode == STYLE_POSTS else STYLE_ARTICLES
        message_id = await self._bot.send_message(
            chat_id, _question(mode), reply_markup=_collect_keyboard(mode)
        )
        self._collected[(workspace.id, chat_id, user_id)] = _Collected()
        replaced = await self._pending.put(
            workspace.id,
            chat_id,
            user_id,
            PendingInput(STYLE_SAMPLES_KIND, mode, message_id),
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
        collected = self._collected.setdefault((workspace.id, chat_id, user_id), _Collected())
        warning = await self._accept(collected, sample)
        text = f"{_question(pending.target_id)}\n\n{_tally(collected)}"
        if warning:
            text += f"\n\n{warning}"
        try:
            await self._bot.edit_message_text(
                chat_id,
                pending.prompt_message_id,
                text,
                reply_markup=_collect_keyboard(pending.target_id),
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
        collected = self._collected.get(key) or _Collected()
        mode = pending.target_id
        if not collected.texts and not collected.links:
            await self._pending.put(workspace.id, chat_id, user_id, pending)
            return STYLE_NOTHING_YET
        if not collected.links and collected.chars < MIN_CHARS:
            await self._pending.put(workspace.id, chat_id, user_id, pending)
            text = STYLE_TOO_SHORT.format(chars=collected.chars, minimum=MIN_CHARS)
            await self._bot.edit_message_text(
                chat_id,
                message_id,
                f"{_question(mode)}\n\n{text}",
                reply_markup=_collect_keyboard(mode, presets=True),
            )
            return None
        self._collected.pop(key, None)
        await self._bot.edit_message_text(chat_id, message_id, STYLE_WORKING)
        await self._jobs.enqueue(
            workspace.id,
            ANALYZE_STYLE_JOB,
            {
                "samples": collected.texts,
                "links": collected.links,
                "source": "posts" if mode == STYLE_POSTS else "articles",
                "mode": mode,
                "message_id": message_id,
            },
            idempotency_key=f"{ANALYZE_STYLE_JOB}:{chat_id}:{message_id}",
        )
        return None

    async def drop(self, workspace: Workspace, chat_id: int, user_id: int) -> None:
        """«🎨 Готовый стиль» / «Отмена»: stop collecting and forget what arrived. The question
        message is left to the caller (it becomes the chooser, or is deleted)."""
        self._collected.pop((workspace.id, chat_id, user_id), None)
        await self._pending.take(workspace.id, chat_id, user_id, STYLE_SAMPLES_KIND)

    async def _accept(self, collected: _Collected, sample: IncomingSample) -> str | None:
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
        if _is_dzen(link):
            return STYLE_WARN_DZEN
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
        if document.file_size is not None and document.file_size > MAX_FILE_BYTES:
            return _Refusal(STYLE_WARN_FILE_BIG)
        data = await self._download(document.file_id)
        if len(data) > MAX_FILE_BYTES:
            return _Refusal(STYLE_WARN_FILE_BIG)
        text = document_text(data, document.file_name)
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
        mode = str(job.payload.get("mode") or STYLE_ARTICLES)
        text, keyboard = render_result(job.job_id, mode, output)
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
        """«✅ Да, это я»: save that very Job's profile; `False` if it has none."""
        profile = await self._profile(workspace.id, job_text)
        if profile is None:
            return False
        await self._settings.set_style_profile(workspace.id, profile)
        await self._bot.edit_message_text(
            chat_id,
            message_id,
            STYLE_SAVED.format(summary=render_profile(profile)),
            reply_markup=await self._next_keyboard(workspace),
        )
        return True

    async def ask_fix(
        self, workspace: Workspace, chat_id: int, user_id: int, job_text: str
    ) -> bool:
        """«✏️ Поправить»: ask for the Владелец's own words about that Job's profile."""
        if await self._profile(workspace.id, job_text) is None:
            return False
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


@dataclass(frozen=True)
class _Refusal:
    warning: str
