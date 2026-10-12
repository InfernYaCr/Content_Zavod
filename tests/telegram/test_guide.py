"""The Инструкция (#114): slides, the carousel, and pictures with their file_id cache and text
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
    GUIDE_SLIDES,
    Guide,
    build_guide_keyboard,
    build_slides,
    clamp_index,
    example_of,
    parse_slide_id,
)
from content_zavod.telegram.settings_screen import SETTING_FIELDS
from content_zavod.telegram.texts import ONBOARDING_NICHE_QUESTION

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


# --- slides ---


@pytest.mark.parametrize("slide", GUIDE_SLIDES, ids=lambda slide: slide.key)
def test_every_caption_fits_a_photo_caption(slide) -> None:
    assert 0 < len(slide.caption) <= CAPTION_LIMIT


def test_slides_are_the_owners_feed_in_order() -> None:
    """#118: one feed for everyone - no Роли, Направления or team slides."""
    assert [slide.title for slide in GUIDE_SLIDES] == [
        "🤖 Что делает бот",
        "🗺 Как я нахожу Темы",
        "🗓 Как проходит неделя",
        "🎯 Ниша",
        "👥 Аудитория",
        "🎙 Стиль автора",
        "🔗 Ваш канал или сайт",
        "🕘 Расписание",
        "✍️ Предложить Тему",
        "📄 Карточка Статьи",
        "❓ Частые вопросы",
    ]
    keys = [slide.key for slide in GUIDE_SLIDES]
    assert len(keys) == len(set(keys))


@pytest.mark.parametrize("name", [slide.key for slide in GUIDE_SLIDES] + list(BANNERS))
def test_every_picture_is_committed_and_light(name: str) -> None:
    path = ASSETS_DIR / f"{name}.png"
    assert path.is_file(), f"run scripts/render_guide_assets.py to draw {name}.png"
    assert path.stat().st_size < 250 * 1024
    width, height = png_size(path)
    assert width >= 800 and width / height <= 2.5


def test_setting_slides_reuse_the_bots_own_words() -> None:
    by_key = {slide.key: slide for slide in GUIDE_SLIDES}
    for field in SETTING_FIELDS:
        caption = by_key[field.key].caption
        assert field.label in caption
        assert field.change_button in caption  # «Где поменять» names the real button
        assert field.purpose[1:] in caption  # what it drives, capitalised
        assert "👍 Так хорошо:" in caption and "👎 Так не надо:" in caption
    assert example_of(ONBOARDING_NICHE_QUESTION) == "фитнес и здоровое питание"
    assert "фитнес и здоровое питание" in by_key["niche"].caption
    assert "(необязательно)" in by_key["audience"].caption
    assert "(необязательно)" not in by_key["niche"].caption
    assert "(необязательно)" in by_key["project"].caption
    assert "✏️ Изменить канал или сайт" in by_key["project"].caption


def test_a_setting_this_build_lacks_has_no_slide() -> None:
    keys = [slide.key for slide in build_slides([f for f in SETTING_FIELDS if f.key != "persona"])]
    assert "persona" not in keys and "niche" in keys


# --- the carousel's keyboard ---


def test_keyboard_wraps_round_and_returns_to_the_menu() -> None:
    markup = build_guide_keyboard(0, 5)

    assert button_texts(markup) == [["◀", "1/5", "▶"], ["🏠 В меню"]]
    assert button_data(markup) == [["gs:4", "gs:0", "gs:1"], ["mn:g"]]
    assert button_data(build_guide_keyboard(4, 5))[0] == ["gs:3", "gs:4", "gs:0"]
    assert all(
        len(data.encode()) <= CALLBACK_DATA_LIMIT for row in button_data(markup) for data in row
    )


def test_a_single_slide_has_only_its_counter() -> None:
    assert button_texts(build_guide_keyboard(0, 1)) == [["1/1"], ["🏠 В меню"]]


@pytest.mark.parametrize(("index", "expected"), [(-3, 0), (0, 0), (4, 4), (10, 4)], ids=str)
def test_a_stale_index_is_clamped(index: int, expected: int) -> None:
    assert clamp_index(index, 5) == expected


def test_slide_ids_carry_the_text_mode_and_garbage_is_the_first_slide() -> None:
    assert parse_slide_id("3") == (3, False)
    assert parse_slide_id("3t") == (3, True)
    assert parse_slide_id("x") == (0, False)


def test_a_text_carousels_buttons_remember_it_is_text() -> None:
    markup = build_guide_keyboard(1, 3, text_mode=True)

    assert button_data(markup)[0] == ["gs:0t", "gs:1t", "gs:2t"]


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
    keyboard = build_guide_keyboard(0, 2)

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


async def test_send_opens_the_carousel_on_the_first_slide() -> None:
    bot = RecordingBot()

    await make_guide(bot).send(CHAT)

    ((chat, photo, caption, markup),) = bot.photos
    assert chat == CHAT and isinstance(photo, BufferedInputFile)
    assert photo.filename == "about.png"
    assert caption == GUIDE_SLIDES[0].caption
    assert button_texts(markup)[0] == ["◀", f"1/{len(GUIDE_SLIDES)}", "▶"]


async def test_show_turns_the_page_in_place_with_the_cached_picture() -> None:
    bot, store = RecordingBot(), FakeStore()
    guide = make_guide(bot, store)

    await guide.show(CHAT, 55, "3")
    await guide.show(CHAT, 55, "3")

    (_, message_id, photo, caption, markup), (*_, again, _, _) = bot.edited_media
    assert message_id == 55 and isinstance(photo, BufferedInputFile)
    assert photo.filename == "niche.png"
    assert caption.startswith("🎯 Ниша")
    assert button_texts(markup)[0][1] == f"4/{len(GUIDE_SLIDES)}"
    assert again == "file-edit-55"  # the file_id Telegram returned, not a second upload


async def test_a_stale_index_lands_on_the_last_slide() -> None:
    """A button from a carousel that had more slides (Роли, Направления, #118)."""
    bot = RecordingBot()

    await make_guide(bot).show(CHAT, 55, "99")

    (_, _, photo, caption, _) = bot.edited_media[0]
    assert photo.filename == "faq.png"
    assert caption.startswith("❓")


async def test_a_photo_that_cannot_go_sends_a_text_carousel_that_turns_as_text() -> None:
    bot = FailingPhotosBot()
    guide = make_guide(bot)

    await guide.send(CHAT)
    ((_, _, markup, _),) = bot.sent
    next_id = button_data(markup)[0][2].removeprefix("gs:")
    await guide.show(CHAT, 100, next_id)

    assert next_id == "1t"
    assert bot.edited_media == []  # no wasted editMessageMedia (and no upload) on a text one
    ((_, message_id, text, turned),) = bot.edited
    assert message_id == 100 and text.startswith("🗺")
    assert button_data(turned)[0][1] == "gs:1t"


async def test_a_photo_carousel_that_turned_out_text_is_redrawn_as_text() -> None:
    class TextOnlyBot(RecordingBot):
        async def edit_message_media(self, *args, **kwargs):
            raise RuntimeError("there is no media in the message to edit")

    bot = TextOnlyBot()

    await make_guide(bot).show(CHAT, 55, "1")

    ((_, message_id, text, markup),) = bot.edited
    assert message_id == 55 and text.startswith("🗺")
    assert button_data(markup)[0][1] == "gs:1t"  # from now on, turned as text


async def test_show_sends_a_new_carousel_when_the_old_one_is_gone() -> None:
    bot = RecordingBot()
    bot.fail_edits = True

    await make_guide(bot).show(CHAT, 55, "1")

    assert bot.photos[0][2].startswith("🗺")


async def test_a_gone_message_is_not_reuploaded_to(tmp_path: Path) -> None:
    class GoneBot(RecordingBot):
        def __init__(self) -> None:
            super().__init__()
            self.media_attempts = 0

        async def edit_message_media(self, chat_id, message_id, *args, **kwargs):
            self.media_attempts += 1
            raise MessageGone(chat_id, message_id)

    bot, store = GoneBot(), FakeStore()
    directory = make_assets(tmp_path, about=b"png")
    await AssetPhotos(RecordingBot(), store, directory=directory).send(CHAT, "about", "x")

    edited = await AssetPhotos(bot, store, directory=directory).edit(CHAT, 55, "about", "x")

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
