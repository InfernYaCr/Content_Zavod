"""The Инструкция (#114, #152): the first screen, its answers, and pictures with their file_id cache and text
fallback."""

from __future__ import annotations

import importlib.util
import json
import struct
import sys
from pathlib import Path

import pytest
from aiogram.types import BufferedInputFile

from content_zavod.telegram.asset_photos import ASSETS_DIR, FILE_ID_KEY_PREFIX, AssetPhotos
from content_zavod.telegram.callback_codec import CALLBACK_DATA_LIMIT
from content_zavod.telegram.gateway import MessageGone
from content_zavod.telegram.guide import (
    BANNERS,
    CAPTION_LIMIT,
    GUIDE_ANSWERS,
    START_SCREEN,
    Guide,
    build_answer_keyboard,
    build_answers,
    build_start_keyboard,
    example_of,
    parse_screen_id,
)
from content_zavod.telegram.texts import ONBOARDING_NICHE_QUESTION
from content_zavod.workspaces import TrialLimits

from .fakes import RecordingBot, button_data, button_texts

CHAT = 7


class FakeStore:
    def __init__(self, values: dict[str, str] | None = None) -> None:
        self.values = dict(values or {})

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def set(self, key: str, value: str) -> None:
        self.values[key] = value


class FailingPhotosBot(RecordingBot):
    """Telegram refuses every photo (or only photos sent by a cached `file_id`)."""

    def __init__(self, *, only_file_ids: bool = False) -> None:
        super().__init__()
        self.only_file_ids = only_file_ids
        self.photo_attempts: list[object] = []

    async def send_photo(self, chat_id, photo, caption=None, reply_markup=None):
        self.photo_attempts.append(photo)
        if not self.only_file_ids or isinstance(photo, str):
            raise RuntimeError("Bad Request: wrong file identifier")
        return await super().send_photo(chat_id, photo, caption, reply_markup)


def png_size(path: Path) -> tuple[int, int]:
    data = path.read_bytes()[:24]
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    return struct.unpack(">II", data[16:24])


def make_assets(tmp_path: Path, **files: bytes) -> Path:
    for name, data in files.items():
        (tmp_path / f"{name}.png").write_bytes(data)
    return tmp_path


# --- screens ---

ALL_SCREENS = (START_SCREEN, *GUIDE_ANSWERS)


@pytest.mark.parametrize("screen", ALL_SCREENS, ids=lambda screen: screen.key)
def test_every_caption_fits_a_photo_caption(screen) -> None:
    assert 0 < len(screen.caption) <= CAPTION_LIMIT


def test_the_first_screen_is_the_owners_3_steps() -> None:
    assert START_SCREEN.caption.startswith("Как это работает — в 3 шага:\n1️⃣")
    assert START_SCREEN.caption.endswith("Что непонятно? Нажмите кнопку ниже.")
    assert START_SCREEN.picture == "welcome"


def test_answers_are_the_owners_questions_in_order() -> None:
    assert [answer.title for answer in GUIDE_ANSWERS] == [
        "🗓 Как проходит неделя",
        "📄 Что делать со Статьёй",
        "🎯 Ниша и Аудитория",
        "🎙 Стиль автора",
        "🔗 Ссылка в конце Статьи",
        "🕘 Расписание",
        "🎁 Лимит",
        "❓ Частые вопросы",
    ]
    keys = [answer.key for answer in ALL_SCREENS]
    assert len(keys) == len(set(keys))


@pytest.mark.parametrize("answer", GUIDE_ANSWERS, ids=lambda answer: answer.key)
def test_an_answer_is_one_short_screen(answer) -> None:
    assert answer.caption.startswith(f"{answer.title}\n\n")
    assert len(answer.body.splitlines()) <= 8  # ≤ 6 lines of text, blank lines aside
    assert len([line for line in answer.body.splitlines() if line]) <= 6


def test_only_week_and_article_have_their_own_picture() -> None:
    assert {
        answer.key: answer.picture for answer in GUIDE_ANSWERS if answer.picture != "welcome"
    } == {
        "week": "week",
        "article": "article",
    }


def test_answers_use_the_bots_own_buttons() -> None:
    by_key = {answer.key: answer.caption for answer in GUIDE_ANSWERS}
    for button in ("📄 Статья для Дзена ›", "✅ Готово, публикую", "✏️ Попросить доработать"):
        assert button in by_key["article"]
    assert "⬇️ Word (.docx)" in by_key["article"]
    assert "«✅ Утвердить всё»" in by_key["week"] and "🔁 Повторить" in by_key["week"]
    assert example_of(ONBOARDING_NICHE_QUESTION) in by_key["niche"]
    assert "✏️ Изменить стиль автора" in by_key["style"] and "📄 Пришлю статью" in by_key["style"]
    assert "✏️ Изменить канал или сайт" in by_key["link"]
    assert "понедельник, 09:00" in by_key["schedule"] and "часовой пояс" in by_key["schedule"]
    for words in ("🔄", "✏️ Попросить доработать", "🗂 История", "📋 План недели"):
        assert words in by_key["faq"]
    assert "«⚠️ Источники не найдены»" in by_key["faq"]


def test_the_limit_answer_follows_the_config() -> None:
    default = {answer.key: answer.caption for answer in GUIDE_ANSWERS}["limit"]
    assert "1 План и 8 Статей" in default and "напишите" not in default
    assert "«✏️ Попросить доработать» тоже тратит одну Статью" in default

    (limit,) = [
        answer.caption
        for answer in build_answers(TrialLimits(plans=2, articles=12, contact="@boss"))
        if answer.key == "limit"
    ]
    assert "2 Плана и 12 Статей" in limit
    assert limit.endswith("Нужно больше — напишите @boss.")


PICTURES = sorted({answer.picture for answer in GUIDE_ANSWERS} | set(BANNERS))


@pytest.mark.parametrize("name", PICTURES)
def test_every_picture_is_committed_and_light(name: str) -> None:
    path = ASSETS_DIR / f"{name}.png"
    assert path.is_file(), f"run scripts/render_guide_assets.py to draw {name}.png"
    assert path.stat().st_size < 250 * 1024
    width, height = png_size(path)
    assert width >= 800 and width / height <= 2.5


def test_no_picture_is_left_unused() -> None:
    """#152: the old carousel's slides went with it."""
    assert sorted(path.stem for path in ASSETS_DIR.glob("*.png")) == PICTURES


# --- keyboards ---


def test_the_first_screen_has_the_questions_two_in_a_row_and_the_menu() -> None:
    markup = build_start_keyboard()

    assert button_texts(markup) == [
        ["🗓 Как проходит неделя", "📄 Что делать со Статьёй"],
        ["🎯 Ниша и Аудитория", "🎙 Стиль автора"],
        ["🔗 Ссылка в конце Статьи", "🕘 Расписание"],
        ["🎁 Лимит", "❓ Частые вопросы"],
        ["🏠 В меню"],
    ]
    assert button_data(markup)[0] == ["gs:week", "gs:article"]
    assert button_data(markup)[-1] == ["mn:g"]
    assert all(
        len(data.encode()) <= CALLBACK_DATA_LIMIT
        for row in button_data(build_start_keyboard(text_mode=True))
        for data in row
    )


def test_an_answer_goes_back_to_the_questions_or_the_menu() -> None:
    markup = build_answer_keyboard()

    assert button_texts(markup) == [["◀ К вопросам", "🏠 В меню"]]
    assert button_data(markup) == [["gs:start", "mn:g"]]


def test_screen_ids_carry_the_text_mode() -> None:
    assert parse_screen_id("week") == ("week", False)
    assert parse_screen_id("week.t") == ("week", True)
    assert parse_screen_id("3t") == ("3t", False)  # an old carousel's id: the first screen
    assert button_data(build_answer_keyboard(text_mode=True))[0][0] == "gs:start.t"
    assert button_data(build_start_keyboard(text_mode=True))[0][0] == "gs:week.t"


# --- pictures: file_id cache and fallbacks ---


async def test_first_send_uploads_then_reuses_the_file_id(tmp_path: Path) -> None:
    bot, store = RecordingBot(), FakeStore()
    photos = AssetPhotos(bot, store, directory=make_assets(tmp_path, welcome=b"png-1"))

    first = await photos.send(CHAT, "welcome", "Привет")
    second = await photos.send(CHAT, "welcome", "Привет")

    assert isinstance(bot.photos[0][1], BufferedInputFile)
    assert bot.photos[1][1] == f"file-{first}"
    assert second != first
    assert store.values[FILE_ID_KEY_PREFIX + "welcome"].endswith(f":file-{first}")


async def test_a_replaced_picture_is_uploaded_again(tmp_path: Path) -> None:
    bot, store = RecordingBot(), FakeStore()
    directory = make_assets(tmp_path, welcome=b"png-1")
    photos = AssetPhotos(bot, store, directory=directory)
    await photos.send(CHAT, "welcome", "Привет")

    (directory / "welcome.png").write_bytes(b"png-2 from a designer")
    await photos.send(CHAT, "welcome", "Привет")

    assert isinstance(bot.photos[1][1], BufferedInputFile)


async def test_a_refused_file_id_falls_back_to_an_upload(tmp_path: Path) -> None:
    bot = FailingPhotosBot(only_file_ids=True)
    store = FakeStore({FILE_ID_KEY_PREFIX + "welcome": "stale:gone"})
    directory = make_assets(tmp_path, welcome=b"png-1")
    photos = AssetPhotos(bot, store, directory=directory)
    # Same bytes, so the cached id is tried first.
    await AssetPhotos(RecordingBot(), store, directory=directory).send(CHAT, "welcome", "x")
    store.values[FILE_ID_KEY_PREFIX + "welcome"] = (
        store.values[FILE_ID_KEY_PREFIX + "welcome"].partition(":")[0] + ":revoked"
    )

    await photos.send(CHAT, "welcome", "Привет")

    assert bot.photo_attempts[0] == "revoked"
    assert isinstance(bot.photo_attempts[1], BufferedInputFile)
    assert bot.sent == []


async def test_a_photo_telegram_refuses_goes_out_as_text_with_its_buttons(tmp_path: Path) -> None:
    bot = FailingPhotosBot()
    photos = AssetPhotos(bot, FakeStore(), directory=make_assets(tmp_path, welcome=b"png"))
    keyboard = build_answer_keyboard()

    message_id = await photos.send(CHAT, "welcome", "Привет", keyboard)

    assert bot.sent == [(CHAT, "Привет", keyboard, None)]
    assert message_id == 100


async def test_a_missing_picture_goes_out_as_text(tmp_path: Path) -> None:
    bot = RecordingBot()
    photos = AssetPhotos(bot, FakeStore(), directory=tmp_path)

    await photos.send(CHAT, "nope", "Привет")

    assert bot.photos == [] and bot.sent[0][1] == "Привет"


# --- Guide ---


def make_guide(bot: RecordingBot, store: FakeStore | None = None) -> Guide:
    return Guide(AssetPhotos(bot, store or FakeStore()), bot)


async def test_send_opens_the_first_screen() -> None:
    bot = RecordingBot()

    await make_guide(bot).send(CHAT)

    ((chat, photo, caption, markup),) = bot.photos
    assert chat == CHAT and isinstance(photo, BufferedInputFile)
    assert photo.filename == "welcome.png"
    assert caption == START_SCREEN.caption
    assert button_texts(markup)[0] == ["🗓 Как проходит неделя", "📄 Что делать со Статьёй"]


async def test_a_question_switches_picture_and_caption_in_place() -> None:
    bot, store = RecordingBot(), FakeStore()
    guide = make_guide(bot, store)

    await guide.show(CHAT, 55, "week")
    await guide.show(CHAT, 55, "start")
    await guide.show(CHAT, 55, "week")

    (_, message_id, photo, caption, markup), back, (*_, again, _, _) = bot.edited_media
    assert message_id == 55 and isinstance(photo, BufferedInputFile)
    assert photo.filename == "week.png" and caption.startswith("🗓 Как проходит неделя")
    assert button_texts(markup) == [["◀ К вопросам", "🏠 В меню"]]
    assert back[2].filename == "welcome.png" and back[3] == START_SCREEN.caption
    assert again == "file-edit-55"  # the file_id Telegram returned, not a second upload


@pytest.mark.parametrize("answer", GUIDE_ANSWERS, ids=lambda answer: answer.key)
async def test_every_answer_opens_with_its_picture_and_the_way_back(answer) -> None:
    bot = RecordingBot()

    await make_guide(bot).show(CHAT, 55, answer.key)

    ((_, _, photo, caption, markup),) = bot.edited_media
    assert photo.filename == f"{answer.picture}.png" and caption == answer.caption
    assert button_data(markup) == [["gs:start", "mn:g"]]


@pytest.mark.parametrize("old_id", ["0", "3", "10", "99", "3t", "x", ""])
async def test_an_old_carousel_button_opens_the_first_screen(old_id: str) -> None:
    """The 11-slide carousel's «gs:N» / «gs:Nt» still in the chat history (#152)."""
    bot = RecordingBot()

    await make_guide(bot).show(CHAT, 55, old_id)

    ((_, _, photo, caption, _),) = bot.edited_media
    assert photo.filename == "welcome.png" and caption == START_SCREEN.caption


async def test_a_photo_that_cannot_go_sends_a_text_guide_that_switches_as_text() -> None:
    bot = FailingPhotosBot()
    guide = make_guide(bot)

    await guide.send(CHAT)
    ((_, _, markup, _),) = bot.sent
    week_id = button_data(markup)[0][0].removeprefix("gs:")
    await guide.show(CHAT, 100, week_id)

    assert week_id == "week.t"
    assert bot.edited_media == []  # no wasted editMessageMedia (and no upload) on a text one
    ((_, message_id, text, switched),) = bot.edited
    assert message_id == 100 and text.startswith("🗓")
    assert button_data(switched)[0][0] == "gs:start.t"


async def test_an_old_text_carousel_is_redrawn_as_the_first_screen_in_text() -> None:
    class TextOnlyBot(RecordingBot):
        async def edit_message_media(self, *args, **kwargs):
            raise RuntimeError("there is no media in the message to edit")

    bot = TextOnlyBot()

    await make_guide(bot).show(CHAT, 55, "1t")

    ((_, message_id, text, markup),) = bot.edited
    assert message_id == 55 and text == START_SCREEN.caption
    assert button_data(markup)[0][0] == "gs:week.t"  # from now on, switched as text


async def test_show_sends_a_new_guide_when_the_old_one_is_gone() -> None:
    bot = RecordingBot()
    bot.fail_edits = True

    await make_guide(bot).show(CHAT, 55, "faq")

    assert bot.photos[0][2].startswith("❓ Частые вопросы")


async def test_a_gone_message_is_not_reuploaded_to(tmp_path: Path) -> None:
    class GoneBot(RecordingBot):
        def __init__(self) -> None:
            super().__init__()
            self.media_attempts = 0

        async def edit_message_media(self, chat_id, message_id, *args, **kwargs):
            self.media_attempts += 1
            raise MessageGone(chat_id, message_id)

    bot, store = GoneBot(), FakeStore()
    directory = make_assets(tmp_path, week=b"png")
    await AssetPhotos(RecordingBot(), store, directory=directory).send(CHAT, "week", "x")

    edited = await AssetPhotos(bot, store, directory=directory).edit(CHAT, 55, "week", "x")

    assert edited is False and bot.media_attempts == 1


# --- the pictures follow the texts ---


def _render_script():
    path = Path(__file__).resolve().parents[2] / "scripts" / "render_guide_assets.py"
    spec = importlib.util.spec_from_file_location("render_guide_assets", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # its dataclasses look their module up
    spec.loader.exec_module(module)
    return module


async def test_every_picture_was_rendered_from_the_current_texts() -> None:
    """A text, button or Настройка changed in the code but its picture not re-rendered: run
    `uv run --with playwright==1.56.0 python scripts/render_guide_assets.py`."""
    script = _render_script()
    recorded = json.loads((ASSETS_DIR / script.SOURCES_FILE).read_text(encoding="utf-8"))

    current = {
        picture.name: script.source_digest(picture) for picture in await script.pictures(None)
    }

    stale = sorted(name for name in current if recorded.get(name) != current[name])
    assert not stale, f"re-render these pictures: {', '.join(stale)}"
    assert set(recorded) == set(current)
