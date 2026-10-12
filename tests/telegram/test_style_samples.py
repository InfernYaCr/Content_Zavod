"""Стиль автора по образцам (#134): collecting Образцы, the result and its buttons, on fakes."""

from __future__ import annotations

from io import BytesIO

import pytest
from docx import Document

from content_zavod.settings import SettingsService
from content_zavod.telegram.input_prompt import InputPrompt
from content_zavod.telegram.style_samples import (
    ANALYZE_STYLE_JOB,
    MAX_FILE_BYTES,
    STYLE_FIX_KIND,
    STYLE_SAMPLES_KIND,
    IncomingSample,
    SampleDocument,
    StyleSamples,
    document_text,
    render_result,
    sample_link,
)
from content_zavod.telegram.texts import (
    STALE_BUTTON,
    STYLE_FAILED,
    STYLE_NOTHING_YET,
    STYLE_WARN_DZEN,
    STYLE_WARN_FILE_BIG,
    STYLE_WARN_FILE_TYPE,
    STYLE_WARN_FILE_UNREADABLE,
    STYLE_WARN_LINKS,
    STYLE_WORKING,
)
from content_zavod.workspaces import Workspace
from tests.tenancy import WS_A, WS_B

from .fakes import FakeJobs, FakePendingInputs, RecordingBot, button_data, button_texts

CHAT = USER = 42
PART = ("Я пишу коротко и по делу, без воды и канцелярита. " * 12).strip()  # 599 chars
PROFILE_OUTPUT = {
    "status": "ok",
    "profile": {
        "traits": {"tone": "прямой", "address": "на «вы»"},
        "summary": "Вы пишете коротко и по делу.",
        "voice_examples": ["без воды и канцелярита"],
        "confidence": "high",
    },
    "chars": 4000,
    "unread": [],
    "source": "articles",
}


class WorkspaceStore:
    def __init__(self) -> None:
        self.values: dict[tuple[int, str], str] = {}

    async def get(self, workspace_id: int, key: str) -> str | None:
        return self.values.get((workspace_id, key))

    async def set(self, workspace_id: int, key: str, value: str) -> None:
        self.values[(workspace_id, key)] = value


class Onboarding:
    def __init__(self, needed: bool = False) -> None:
        self.is_needed = needed

    async def needed(self, workspace_id: int) -> bool:
        return self.is_needed


class Env:
    def __init__(self) -> None:
        self.bot = RecordingBot()
        self.pending = FakePendingInputs()
        self.jobs = FakeJobs()
        self.store = WorkspaceStore()
        self.settings = SettingsService(self.store)
        self.files: dict[str, bytes] = {}
        self.downloaded: list[str] = []
        self.onboarding = Onboarding()
        self.style = StyleSamples(
            self.jobs,
            self.settings,
            self.pending,
            InputPrompt(self.bot, self.pending),
            self.bot,
            self.download,
            self.onboarding,
        )
        self.a = Workspace(WS_A, USER, None, "Europe/Moscow", None, None)
        self.b = Workspace(WS_B, USER, None, "Europe/Moscow", None, None)

    async def download(self, file_id: str) -> bytes:
        self.downloaded.append(file_id)
        return self.files[file_id]

    async def send(self, text: str, workspace: Workspace | None = None) -> bool:
        return await self.style.handle_message(
            workspace or self.a, CHAT, USER, IncomingSample(text=text)
        )

    async def send_file(self, name: str, data: bytes | None = None, size: int | None = None):
        if data is not None:
            self.files[name] = data
        document = SampleDocument(name, name, size if size is not None else len(data or b""))
        return await self.style.handle_message(
            self.a, CHAT, USER, IncomingSample(text=None, document=document)
        )

    @property
    def question_id(self) -> int:
        return self.pending.rows[(CHAT, USER)].prompt_message_id

    @property
    def last_edit(self) -> str:
        return self.bot.edited[-1][2]


@pytest.fixture
def env() -> Env:
    return Env()


def _docx(text: str) -> bytes:
    document = Document()
    for paragraph in text.split("\n"):
        document.add_paragraph(paragraph)
    buffer = BytesIO()
    document.save(buffer)
    return buffer.getvalue()


# --- collecting until «Готово» ---


async def test_long_text_in_several_messages_is_collected_until_done(env: Env) -> None:
    await env.style.start(env.a, CHAT, USER, "a")
    question = env.question_id
    (_, ask, markup, _) = env.bot.sent[-1]
    assert "Пришлите 1–3 свои статьи" in ask
    assert button_data(markup) == [["yd:a"], [f"ci:{STYLE_SAMPLES_KIND}"]]

    for _ in range(3):
        assert await env.send(PART)
    assert "Получено: 1797 знаков текста" in env.last_edit

    assert await env.style.done(env.a, CHAT, USER, question) is None

    (job,) = env.jobs.jobs.values()
    assert job.job_type == ANALYZE_STYLE_JOB
    assert job.payload["samples"] == [PART.strip()] * 3
    assert (job.payload["source"], job.payload["message_id"]) == ("articles", question)
    assert env.bot.edited[-1][1:3] == (question, STYLE_WORKING)
    assert (CHAT, USER) not in env.pending.rows
    assert not await env.send(PART)  # the wait is closed: no longer an Образец


async def test_under_1500_chars_no_job_and_the_presets_are_offered(env: Env) -> None:
    await env.style.start(env.a, CHAT, USER, "a")
    await env.send(PART)

    assert await env.style.done(env.a, CHAT, USER, env.question_id) is None

    assert env.jobs.jobs == {}
    assert "Маловато текста: 599 знаков" in env.last_edit
    assert ["yp:"] in button_data(env.bot.edited[-1][3])
    assert await env.send(PART * 2)  # still collecting
    await env.style.done(env.a, CHAT, USER, env.question_id)
    assert len(env.jobs.jobs) == 1


async def test_done_with_nothing_sent_says_so(env: Env) -> None:
    await env.style.start(env.a, CHAT, USER, "a")

    assert await env.style.done(env.a, CHAT, USER, env.question_id) == STYLE_NOTHING_YET
    assert env.pending.rows[(CHAT, USER)].kind == STYLE_SAMPLES_KIND


async def test_a_stale_done_starts_nothing(env: Env) -> None:
    assert await env.style.done(env.a, CHAT, USER, 77) == STALE_BUTTON
    assert env.jobs.jobs == {}


async def test_posts_ask_for_consent_and_are_sent_as_posts(env: Env) -> None:
    await env.style.start(env.a, CHAT, USER, "p")
    (_, ask, markup, _) = env.bot.sent[-1]
    assert "вы подтверждаете, что канал ваш" in ask
    assert button_texts(markup)[0] == ["✅ Это мой канал — готово"]

    await env.send("t.me/my_channel")
    await env.style.done(env.a, CHAT, USER, env.question_id)

    (job,) = env.jobs.jobs.values()
    assert job.payload["links"] == ["https://t.me/my_channel"] and job.payload["samples"] == []
    assert job.payload["source"] == "posts"


async def test_commands_are_not_samples(env: Env) -> None:
    await env.style.start(env.a, CHAT, USER, "a")

    assert not await env.send("/menu")


# --- links ---


@pytest.mark.parametrize(
    ("text", "link"),
    [
        ("t.me/my_channel", "https://t.me/my_channel"),
        ("https://t.me/s/my_channel", "https://t.me/my_channel"),
        ("@my_channel", "https://t.me/my_channel"),
        ("vc.ru/u/1-statya", "https://vc.ru/u/1-statya"),
        ("Привет", None),
        ("одна ссылка vc.ru/x и текст", None),
    ],
)
def test_sample_link(text: str, link: str | None) -> None:
    assert sample_link(text) == link


async def test_a_dzen_link_is_refused_at_once(env: Env) -> None:
    await env.style.start(env.a, CHAT, USER, "a")

    await env.send("https://dzen.ru/a/aBcD")

    assert STYLE_WARN_DZEN in env.last_edit
    await env.send(PART * 3)
    await env.style.done(env.a, CHAT, USER, env.question_id)
    assert next(iter(env.jobs.jobs.values())).payload["links"] == []


async def test_at_most_three_links(env: Env) -> None:
    await env.style.start(env.a, CHAT, USER, "a")
    for index in range(4):
        await env.send(f"https://blog.example/{index}")

    assert STYLE_WARN_LINKS in env.last_edit
    await env.style.done(env.a, CHAT, USER, env.question_id)
    assert len(next(iter(env.jobs.jobs.values())).payload["links"]) == 3


# --- files ---


async def test_a_docx_file_is_read(env: Env) -> None:
    await env.style.start(env.a, CHAT, USER, "a")

    await env.send_file("статья.docx", _docx("Первый абзац.\nВторой абзац."))

    assert "Получено: 27 знаков" in env.last_edit


def test_document_text_reads_txt_md_in_utf8_or_cp1251() -> None:
    assert document_text("Привет".encode(), "a.txt") == "Привет"
    assert document_text("Привет".encode("cp1251"), "a.md") == "Привет"
    assert document_text(b"not a zip", "a.docx") is None


async def test_unsupported_too_big_or_broken_files_are_refused(env: Env) -> None:
    await env.style.start(env.a, CHAT, USER, "a")

    await env.send_file("статья.pdf", b"%PDF")
    assert STYLE_WARN_FILE_TYPE in env.last_edit
    await env.send_file("big.txt", size=MAX_FILE_BYTES + 1)
    assert STYLE_WARN_FILE_BIG in env.last_edit
    await env.send_file("broken.docx", b"PK\x03\x04 broken")
    assert STYLE_WARN_FILE_UNREADABLE in env.last_edit

    assert env.downloaded == ["broken.docx"]  # nothing past the type and size checks
    assert "Получено: 0 знаков" in env.last_edit


# --- one Пространство's collection is never another's ---


async def test_bs_messages_and_done_never_touch_as_collection(env: Env) -> None:
    await env.style.start(env.a, CHAT, USER, "a")
    question = env.question_id
    await env.send(PART * 3)

    assert not await env.send("чужой текст", env.b)
    assert await env.style.done(env.b, CHAT, USER, question) == STALE_BUTTON

    assert env.pending.rows[(CHAT, USER)].kind == STYLE_SAMPLES_KIND  # A's wait still open
    await env.style.done(env.a, CHAT, USER, question)
    (job,) = env.jobs.jobs.values()
    assert "чужой текст" not in job.payload["samples"]


# --- the result ---


async def _finished(env: Env, output: dict | None) -> int:
    await env.style.start(env.a, CHAT, USER, "a")
    await env.send(PART * 3)
    await env.style.done(env.a, CHAT, USER, env.question_id)
    (job_id,) = env.jobs.jobs
    result = env.jobs.finish(job_id, output)
    await env.style.forget_samples(result)
    await env.style.deliver(CHAT, result)
    return job_id


async def test_the_result_shows_the_profile_and_the_samples_are_gone(env: Env) -> None:
    job_id = await _finished(env, PROFILE_OUTPUT)

    _, message_id, text, markup = env.bot.edited[-1]
    assert message_id == env.jobs.jobs[job_id].payload["message_id"]
    assert text.startswith("Вот как вы пишете:\n\nВы пишете коротко и по делу.")
    assert "Тон: прямой" in text
    assert button_data(markup) == [[f"yk:{job_id}"], [f"yf:{job_id}"], ["ya:a"]]
    assert set(env.jobs.jobs[job_id].payload) == {"source", "mode", "message_id"}
    assert env.store.values == {}  # nothing saved before «✅»


def test_failed_and_too_short_results_offer_other_samples_or_presets() -> None:
    text, markup = render_result(1, "p", None)
    assert text == STYLE_FAILED and button_data(markup) == [["ya:p"], ["yp:"]]

    text, _ = render_result(
        1, "a", {"status": "too_short", "chars": 900, "unread": ["https://t.me/closed"]}
    )
    assert "Не хватило текста: 900 знаков" in text and "https://t.me/closed" in text


async def test_confirm_saves_the_profile_and_offers_the_way_on(env: Env) -> None:
    job_id = await _finished(env, PROFILE_OUTPUT)
    env.onboarding.is_needed = True

    assert await env.style.confirm(env.a, CHAT, 500, str(job_id))

    profile = (await env.settings.read(WS_A)).style_profile
    assert profile is not None and profile.summary == "Вы пишете коротко и по делу."
    assert env.bot.edited[-1][1] == 500
    assert button_data(env.bot.edited[-1][3]) == [["ob:project"]]
    assert (await env.settings.read(WS_B)).style_profile is None


async def test_confirm_of_a_failed_or_foreign_job_saves_nothing(env: Env) -> None:
    job_id = await _finished(env, None)

    assert not await env.style.confirm(env.a, CHAT, 500, str(job_id))
    assert not await env.style.confirm(env.b, CHAT, 500, str(job_id))
    assert not await env.style.confirm(env.a, CHAT, 500, "nope")
    assert env.store.values == {}


async def test_fix_saves_the_profile_with_the_owners_corrections(env: Env) -> None:
    job_id = await _finished(env, PROFILE_OUTPUT)

    assert await env.style.ask_fix(env.a, CHAT, USER, str(job_id))
    assert env.pending.rows[(CHAT, USER)].kind == STYLE_FIX_KIND
    assert await env.style.handle_reply(env.a, CHAT, USER, "x" * 501)  # too long: asked again
    assert (await env.settings.read(WS_A)).style_profile is None

    assert await env.style.handle_reply(env.a, CHAT, USER, "Я пишу на «ты»")

    profile = (await env.settings.read(WS_A)).style_profile
    assert profile is not None and profile.corrections == "Я пишу на «ты»"
    assert "Ваши поправки: Я пишу на «ты»" in env.bot.sent[-1][1]
    assert not await env.style.handle_reply(env.a, CHAT, USER, "ещё")  # the wait is closed
