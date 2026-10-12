"""Инструкция (#114): the «📖 Как пользоваться» carousel.

The carousel is one photo message: a picture per slide (`assets/guide/<slide key>.png`) with
the slide's text as its caption (≤ 1024 characters - Telegram's caption limit), turned with
◀ ▶ through `editMessageMedia`, «N/M» in between and «🏠 В меню» below. Every button carries
the slide's index, never a session, and an index out of range is clamped - so a button on an
old carousel (one with more slides than today's) still lands on a real slide.

Everyone sees the same slides: there are no Roles, every user is the Владелец of their own
Пространство (ADR-0015). Each Настройка's slide takes its label, what it drives, its example
and its «Изменить …» button from `SETTING_FIELDS` and the onboarding questions - the Инструкция
shows exactly what the bot asks - and adds a bad example and why.

The carousel opens from the Главное меню, /help, the onboarding intro and «Проверьте вводные».
A carousel opened from the menu replaces it, and «🏠 В меню» replaces the carousel with the
menu again.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from ..scheduling import DEFAULT_DAY_OF_WEEK, DEFAULT_HOUR, DEFAULT_MINUTE
from ..settings import PERSONAS
from .asset_photos import AssetPhotos
from .callback_codec import Action, SimpleAction, encode_callback_data
from .gateway import BotClient
from .guide_texts import (
    GUIDE_ABOUT,
    GUIDE_ABOUT_TITLE,
    GUIDE_ARTICLE,
    GUIDE_ARTICLE_TITLE,
    GUIDE_AUDIENCE_BAD,
    GUIDE_AUDIENCE_BAD_WHY,
    GUIDE_AUDIENCE_TITLE,
    GUIDE_AUDIENCE_WHY,
    GUIDE_BUTTON,
    GUIDE_COUNTER,
    GUIDE_FAQ,
    GUIDE_FAQ_TITLE,
    GUIDE_NEXT_BUTTON,
    GUIDE_NICHE_BAD,
    GUIDE_NICHE_BAD_WHY,
    GUIDE_NICHE_TITLE,
    GUIDE_NICHE_WHY,
    GUIDE_PERSONA_BAD,
    GUIDE_PERSONA_BAD_WHY,
    GUIDE_PERSONA_TITLE,
    GUIDE_PERSONA_WHY,
    GUIDE_PREV_BUTTON,
    GUIDE_PROJECT_BAD,
    GUIDE_PROJECT_BAD_WHY,
    GUIDE_PROJECT_GOOD,
    GUIDE_PROJECT_TITLE,
    GUIDE_PROJECT_WHY,
    GUIDE_PROPOSE,
    GUIDE_PROPOSE_TITLE,
    GUIDE_SCHEDULE_BAD,
    GUIDE_SCHEDULE_BAD_WHY,
    GUIDE_SCHEDULE_GOOD_SUFFIX,
    GUIDE_SCHEDULE_TITLE,
    GUIDE_SCHEDULE_WHY,
    GUIDE_SETTING,
    GUIDE_SETTING_OPTIONAL,
    GUIDE_SOURCING,
    GUIDE_SOURCING_TITLE,
    GUIDE_TO_MENU_BUTTON,
    GUIDE_WEEK,
    GUIDE_WEEK_TITLE,
)
from .settings_screen import SETTING_FIELDS, SettingField
from .texts import (
    ARTICLE_CARD_NO_EVIDENCE,
    HUB_BUTTON_RETRY_TOPIC,
    MENU_HISTORY_BUTTON,
    MENU_PLAN_BUTTON,
    MENU_SCHEDULE_BUTTON,
    MENU_SETTINGS_BUTTON,
    MENU_TOPIC_BUTTON,
    ONBOARDING_AUDIENCE_QUESTION,
    ONBOARDING_NICHE_QUESTION,
    PLAN_GENERATE_BUTTON,
    READ_BUTTON,
    REFINE_BUTTON,
    SCHEDULE_PURPOSE,
    schedule_text,
)

logger = logging.getLogger(__name__)

CAPTION_LIMIT = 1024
"""Telegram's limit on a photo caption."""

FROM_MENU = "m"
"""`guide` id: opened from the Главное меню, which the carousel replaces."""
FROM_GUIDE = "g"
"""`menu` id: «🏠 В меню» pressed on the carousel, which the menu replaces."""
_MENU = "🏠 Меню"


@dataclass(frozen=True)
class Slide:
    key: str
    """Stable name: the picture is `assets/guide/<key>.png`."""
    title: str
    body: str
    optional: bool = False
    """A Настройка the bot works without - the title says «(необязательно)»."""

    @property
    def caption(self) -> str:
        suffix = GUIDE_SETTING_OPTIONAL if self.optional else ""
        return f"{self.title}{suffix}\n\n{self.body}"


def example_of(question: str) -> str:
    """The «Например: …» part of a bot question - up to the next blank line."""
    _, found, rest = question.partition("Например:")
    if not found:
        return ""
    return rest.strip().split("\n\n")[0].strip()


def _capitalized(text: str) -> str:
    return text[:1].upper() + text[1:]


def _setting_slide(
    field: SettingField,
    title: str,
    *,
    why: str,
    good: str,
    bad: str,
    bad_why: str,
    optional: bool = False,
) -> Slide:
    return Slide(
        key=field.key,
        title=title,
        body=GUIDE_SETTING.format(
            purpose=_capitalized(field.purpose),
            why=why,
            good=good,
            bad=bad,
            bad_why=bad_why,
            where=f"{_MENU} → {MENU_SETTINGS_BUTTON} → {field.change_button}",
        ),
        optional=optional,
    )


def _settings_slides(fields: Sequence[SettingField]) -> list[Slide]:
    """One slide per Настройка, in the onboarding's order; a key `fields` lacks is skipped."""
    by_key = {field.key: field for field in fields}
    slides: list[Slide] = []
    if field := by_key.get("niche"):
        slides.append(
            _setting_slide(
                field,
                GUIDE_NICHE_TITLE,
                why=GUIDE_NICHE_WHY,
                good=example_of(ONBOARDING_NICHE_QUESTION),
                bad=GUIDE_NICHE_BAD,
                bad_why=GUIDE_NICHE_BAD_WHY,
            )
        )
    if field := by_key.get("audience"):
        slides.append(
            _setting_slide(
                field,
                GUIDE_AUDIENCE_TITLE,
                why=GUIDE_AUDIENCE_WHY,
                good=example_of(ONBOARDING_AUDIENCE_QUESTION),
                bad=GUIDE_AUDIENCE_BAD,
                bad_why=GUIDE_AUDIENCE_BAD_WHY,
                optional=True,
            )
        )
    if field := by_key.get("persona"):
        presets = ", ".join(f"«{persona.title}»" for persona in PERSONAS.values())
        slides.append(
            _setting_slide(
                field,
                GUIDE_PERSONA_TITLE,
                why=GUIDE_PERSONA_WHY.format(presets=presets),
                good=example_of(field.question),
                bad=GUIDE_PERSONA_BAD,
                bad_why=GUIDE_PERSONA_BAD_WHY,
            )
        )
    if field := by_key.get("project"):
        slides.append(
            _setting_slide(
                field,
                GUIDE_PROJECT_TITLE,
                why=GUIDE_PROJECT_WHY,
                good=GUIDE_PROJECT_GOOD,
                bad=GUIDE_PROJECT_BAD,
                bad_why=GUIDE_PROJECT_BAD_WHY,
                optional=True,
            )
        )
    # Расписание isn't a Настройка, but it lives next to them.
    slides.append(
        Slide(
            key="schedule",
            title=GUIDE_SCHEDULE_TITLE,
            body=GUIDE_SETTING.format(
                purpose=_capitalized(SCHEDULE_PURPOSE),
                why=GUIDE_SCHEDULE_WHY,
                good=schedule_text(DEFAULT_DAY_OF_WEEK, DEFAULT_HOUR, DEFAULT_MINUTE)
                + GUIDE_SCHEDULE_GOOD_SUFFIX,
                bad=GUIDE_SCHEDULE_BAD,
                bad_why=GUIDE_SCHEDULE_BAD_WHY,
                where=f"{_MENU} → {MENU_SCHEDULE_BUTTON}",
            ),
        )
    )
    return slides


def build_slides(fields: Sequence[SettingField] = SETTING_FIELDS) -> tuple[Slide, ...]:
    return (
        Slide("about", GUIDE_ABOUT_TITLE, GUIDE_ABOUT),
        Slide(
            "sourcing",
            GUIDE_SOURCING_TITLE,
            GUIDE_SOURCING.format(regenerate="🔄", topic=MENU_TOPIC_BUTTON),
        ),
        Slide(
            "week",
            GUIDE_WEEK_TITLE,
            GUIDE_WEEK.format(
                regenerate="🔄",
                delete="🗑",
                approve_all="✅ Утвердить всё",
                retry=HUB_BUTTON_RETRY_TOPIC,
                read=READ_BUTTON.split()[0],
                menu=_MENU,
                topic=MENU_TOPIC_BUTTON,
            ),
        ),
        *_settings_slides(fields),
        Slide(
            "propose",
            GUIDE_PROPOSE_TITLE,
            GUIDE_PROPOSE.format(menu=_MENU, topic=MENU_TOPIC_BUTTON),
        ),
        Slide(
            "article",
            GUIDE_ARTICLE_TITLE,
            GUIDE_ARTICLE.format(
                read=READ_BUTTON,
                refine=REFINE_BUTTON,
                # «⚠️ Источники не найдены» - the card's own words, without the advice part.
                no_evidence=ARTICLE_CARD_NO_EVIDENCE["no_evidence"].split(" — ")[0],
            ),
        ),
        Slide(
            "faq",
            GUIDE_FAQ_TITLE,
            GUIDE_FAQ.format(
                topic=MENU_TOPIC_BUTTON,
                plan=MENU_PLAN_BUTTON,
                generate=PLAN_GENERATE_BUTTON,
                regenerate="🔄",
                refine=REFINE_BUTTON,
                menu=_MENU,
                history=MENU_HISTORY_BUTTON,
            ),
        ),
    )


GUIDE_SLIDES: tuple[Slide, ...] = build_slides()

# Every picture the bot sends besides the slides; `scripts/render_guide_assets.py` draws them.
BANNERS: tuple[str, ...] = ("welcome", "onboarding")


def clamp_index(index: int, total: int) -> int:
    return min(max(index, 0), max(total - 1, 0))


TEXT_MODE = "t"
"""Suffix on a `guide_slide` id: this carousel went out as text (its picture couldn't be
sent), so its pages are turned with `editMessageText` straight away."""


def slide_id(index: int, *, text_mode: bool = False) -> str:
    return f"{index}{TEXT_MODE if text_mode else ''}"


def parse_slide_id(text: str) -> tuple[int, bool]:
    """`guide_slide` id -> (slide index, text mode); anything unparsable is the first slide."""
    text_mode = text.endswith(TEXT_MODE)
    try:
        return int(text.removesuffix(TEXT_MODE)), text_mode
    except ValueError:
        return 0, text_mode


def _button(text: str, action: Action, id_: str = "") -> InlineKeyboardButton:
    return InlineKeyboardButton(
        text=text, callback_data=encode_callback_data(SimpleAction(action, id_))
    )


def build_guide_keyboard(
    index: int, total: int, *, text_mode: bool = False
) -> InlineKeyboardMarkup:
    """◀ N/M ▶ (wrapping round) and «🏠 В меню»; «N/M» redraws the slide it shows."""

    def turn(to: int) -> str:
        return slide_id(to % total, text_mode=text_mode)

    counter = _button(
        GUIDE_COUNTER.format(number=index + 1, total=total), "guide_slide", turn(index)
    )
    nav = [counter]
    if total > 1:
        nav = [
            _button(GUIDE_PREV_BUTTON, "guide_slide", turn(index - 1)),
            counter,
            _button(GUIDE_NEXT_BUTTON, "guide_slide", turn(index + 1)),
        ]
    return InlineKeyboardMarkup(
        inline_keyboard=[nav, [_button(GUIDE_TO_MENU_BUTTON, "menu", FROM_GUIDE)]]
    )


def guide_button(*, from_menu: bool = False) -> InlineKeyboardButton:
    """«📖 Как пользоваться» for any keyboard: sends the carousel as a new message (in place
    of the pressed one, `from_menu`)."""
    return _button(GUIDE_BUTTON, "guide", FROM_MENU if from_menu else "")


class Guide:
    def __init__(
        self, photos: AssetPhotos, bot: BotClient, *, slides: Sequence[Slide] = GUIDE_SLIDES
    ) -> None:
        self._photos = photos
        self._bot = bot
        self._slides = tuple(slides)

    def _screen(self, index: int) -> tuple[Slide, InlineKeyboardMarkup, InlineKeyboardMarkup]:
        """The slide, its photo-mode keyboard and its text-mode one."""
        index = clamp_index(index, len(self._slides))
        return (
            self._slides[index],
            build_guide_keyboard(index, len(self._slides)),
            build_guide_keyboard(index, len(self._slides), text_mode=True),
        )

    async def send(self, chat_id: int, index: int = 0) -> None:
        """The carousel as a new message, on slide `index`."""
        slide, keyboard, text_keyboard = self._screen(index)
        await self._photos.send(
            chat_id, slide.key, slide.caption, keyboard, text_markup=text_keyboard
        )

    async def show(self, chat_id: int, message_id: int, id_: str) -> None:
        """Turn the carousel `message_id` to the slide a `guide_slide` id names. A photo
        carousel swaps its picture; one that went out as text (its buttons say so) is turned
        as text; one that can't be edited at all is sent anew."""
        index, text_mode = parse_slide_id(id_)
        slide, keyboard, text_keyboard = self._screen(index)
        if not text_mode and await self._photos.edit(
            chat_id, message_id, slide.key, slide.caption, keyboard
        ):
            return
        try:
            await self._bot.edit_message_text(
                chat_id, message_id, slide.caption, reply_markup=text_keyboard
            )
            return
        except Exception:
            logger.info("could not turn guide message %s", message_id, exc_info=True)
        await self._photos.send(
            chat_id, slide.key, slide.caption, keyboard, text_markup=text_keyboard
        )
