"""Инструкция (#114, #152): «📖 Как пользоваться» - a short start in 3 steps and question
buttons, each opening a short answer.

All of it is one photo message: the first screen and most answers show `welcome.png`, «🗓 Как
проходит неделя» and «📄 Что делать со Статьёй» their own picture (`assets/guide/<key>.png`).
A photo message can't become a text one, so every screen is a picture with the text as its
caption (≤ 1024 characters - Telegram's caption limit), switched with `editMessageMedia`. An
answer has «◀ К вопросам» and «🏠 В меню»; the first screen has the questions and «🏠 В меню».

Every button carries the screen's key, never a session. Any other key - a button of the old
11-slide carousel («gs:3») included - opens the first screen.

The Инструкция opens from the Главное меню, /help, the onboarding intro and «Проверьте вводные».
Opened from the menu it replaces it, and «🏠 В меню» replaces it with the menu again.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from ..domain import PLATFORMS
from ..scheduling import DEFAULT_DAY_OF_WEEK, DEFAULT_HOUR, DEFAULT_MINUTE
from ..workspaces import TrialLimits
from .asset_photos import AssetPhotos
from .callback_codec import Action, SimpleAction, encode_callback_data
from .gateway import BotClient
from .guide_texts import (
    GUIDE_ARTICLE,
    GUIDE_ARTICLE_BUTTON,
    GUIDE_BUTTON,
    GUIDE_FAQ,
    GUIDE_FAQ_BUTTON,
    GUIDE_LIMIT,
    GUIDE_LIMIT_BUTTON,
    GUIDE_LIMIT_CONTACT,
    GUIDE_LINK,
    GUIDE_LINK_BUTTON,
    GUIDE_NICHE,
    GUIDE_NICHE_BUTTON,
    GUIDE_SCHEDULE,
    GUIDE_SCHEDULE_BUTTON,
    GUIDE_START,
    GUIDE_STYLE,
    GUIDE_STYLE_BUTTON,
    GUIDE_TO_MENU_BUTTON,
    GUIDE_TO_QUESTIONS_BUTTON,
    GUIDE_WEEK,
    GUIDE_WEEK_BUTTON,
)
from .settings_screen import SETTING_FIELDS
from .texts import (
    ARTICLE_CARD_NO_EVIDENCE,
    HUB_BUTTON_APPROVE,
    HUB_BUTTON_ARTICLE,
    HUB_BUTTON_DOCX,
    HUB_BUTTON_READ_FULL,
    HUB_BUTTON_REFINE,
    HUB_BUTTON_RETRY_TOPIC,
    MENU_HISTORY_BUTTON,
    MENU_PLAN_BUTTON,
    MENU_SCHEDULE_BUTTON,
    MENU_SETTINGS_BUTTON,
    MENU_TOPIC_BUTTON,
    ONBOARDING_NICHE_QUESTION,
    STYLE_ARTICLES_BUTTON,
    STYLE_POSTS_BUTTON,
    STYLE_PRESETS_BUTTON,
    articles_text,
    plans_text,
    platform_genitive,
    schedule_text,
)

logger = logging.getLogger(__name__)

CAPTION_LIMIT = 1024
"""Telegram's limit on a photo caption."""

FROM_MENU = "m"
"""`guide` id: opened from the Главное меню, which the Инструкция replaces."""
FROM_GUIDE = "g"
"""`menu` id: «🏠 В меню» pressed on the Инструкция, which the menu replaces."""
_MENU = "🏠 Меню"

START = "start"
"""The first screen's key."""
WELCOME = "welcome"
"""The picture of the first screen and of every answer without its own."""


@dataclass(frozen=True)
class Screen:
    key: str
    title: str
    """The question button's label, and the answer's first line; empty on the first screen."""
    body: str
    picture: str = WELCOME
    """`assets/guide/<picture>.png`."""

    @property
    def caption(self) -> str:
        return f"{self.title}\n\n{self.body}" if self.title else self.body


def example_of(question: str) -> str:
    """The «Например: …» part of a bot question - up to the next blank line."""
    _, found, rest = question.partition("Например:")
    if not found:
        return ""
    return rest.strip().split("\n\n")[0].strip()


def _settings_path(key: str) -> str:
    field = next(field for field in SETTING_FIELDS if field.key == key)
    return f"{_MENU} → {MENU_SETTINGS_BUTTON} → {field.change_button}"


_TRIAL = TrialLimits()
"""The Лимит by default (no env) - what the tests and the pictures see."""


def build_answers(trial: TrialLimits = _TRIAL) -> tuple[Screen, ...]:
    """The question screens, in the first screen's button order."""
    article = HUB_BUTTON_ARTICLE.format(platform=platform_genitive(PLATFORMS[0]))
    persona = next(field for field in SETTING_FIELDS if field.key == "persona")
    limit = GUIDE_LIMIT.format(
        plans=plans_text(trial.plans),
        articles=articles_text(trial.articles),
        refine=HUB_BUTTON_REFINE,
        regenerate="🔄",
        menu=_MENU,
    )
    if trial.contact:
        limit += GUIDE_LIMIT_CONTACT.format(contact=trial.contact)
    return (
        Screen(
            "week",
            GUIDE_WEEK_BUTTON,
            GUIDE_WEEK.format(
                regenerate="🔄",
                delete="🗑",
                approve_all="✅ Утвердить всё",
                retry=HUB_BUTTON_RETRY_TOPIC,
                article=article,
            ),
            picture="week",
        ),
        Screen(
            "article",
            GUIDE_ARTICLE_BUTTON,
            GUIDE_ARTICLE.format(
                article=article,
                read=HUB_BUTTON_READ_FULL,
                docx=HUB_BUTTON_DOCX,
                refine=HUB_BUTTON_REFINE,
                approve=HUB_BUTTON_APPROVE,
            ),
            picture="article",
        ),
        Screen(
            "niche",
            GUIDE_NICHE_BUTTON,
            GUIDE_NICHE.format(
                niche_good=example_of(ONBOARDING_NICHE_QUESTION),
                where=f"{_MENU} → {MENU_SETTINGS_BUTTON}",
            ),
        ),
        Screen(
            "style",
            GUIDE_STYLE_BUTTON,
            GUIDE_STYLE.format(
                purpose=persona.purpose[:1].upper() + persona.purpose[1:],
                articles=STYLE_ARTICLES_BUTTON,
                posts=STYLE_POSTS_BUTTON,
                presets=STYLE_PRESETS_BUTTON,
                where=_settings_path("persona"),
            ),
        ),
        Screen("link", GUIDE_LINK_BUTTON, GUIDE_LINK.format(where=_settings_path("project"))),
        Screen(
            "schedule",
            GUIDE_SCHEDULE_BUTTON,
            GUIDE_SCHEDULE.format(
                default=schedule_text(DEFAULT_DAY_OF_WEEK, DEFAULT_HOUR, DEFAULT_MINUTE),
                where=f"{_MENU} → {MENU_SCHEDULE_BUTTON}",
                menu=_MENU,
            ),
        ),
        Screen("limit", GUIDE_LIMIT_BUTTON, limit),
        Screen(
            "faq",
            GUIDE_FAQ_BUTTON,
            GUIDE_FAQ.format(
                regenerate="🔄",
                refine=HUB_BUTTON_REFINE,
                menu=_MENU,
                plan=MENU_PLAN_BUTTON,
                history=MENU_HISTORY_BUTTON,
                # «⚠️ Источники не найдены» - the card's own words, without the advice part.
                no_evidence=ARTICLE_CARD_NO_EVIDENCE["no_evidence"].split(" — ")[0],
                topic=MENU_TOPIC_BUTTON,
            ),
        ),
    )


START_SCREEN = Screen(START, "", GUIDE_START)
GUIDE_ANSWERS: tuple[Screen, ...] = build_answers()

# Every picture the bot sends besides the Инструкция's own; `scripts/render_guide_assets.py`
# draws them.
BANNERS: tuple[str, ...] = (WELCOME, "onboarding")


TEXT_MODE = ".t"
"""Suffix on a `guide_screen` id: this Инструкция went out as text (its picture couldn't be
sent), so its screens are switched with `editMessageText` straight away."""


def screen_id(key: str, *, text_mode: bool = False) -> str:
    return f"{key}{TEXT_MODE if text_mode else ''}"


def parse_screen_id(text: str) -> tuple[str, bool]:
    """`guide_screen` id -> (screen key, text mode)."""
    return text.removesuffix(TEXT_MODE), text.endswith(TEXT_MODE)


def _button(text: str, action: Action, id_: str = "") -> InlineKeyboardButton:
    return InlineKeyboardButton(
        text=text, callback_data=encode_callback_data(SimpleAction(action, id_))
    )


def _to_menu() -> InlineKeyboardButton:
    return _button(GUIDE_TO_MENU_BUTTON, "menu", FROM_GUIDE)


def build_start_keyboard(
    answers: tuple[Screen, ...] = GUIDE_ANSWERS, *, text_mode: bool = False
) -> InlineKeyboardMarkup:
    """The questions, two in a row, and «🏠 В меню»."""
    buttons = [
        _button(answer.title, "guide_screen", screen_id(answer.key, text_mode=text_mode))
        for answer in answers
    ]
    rows = [buttons[i : i + 2] for i in range(0, len(buttons), 2)]
    return InlineKeyboardMarkup(inline_keyboard=[*rows, [_to_menu()]])


def build_answer_keyboard(*, text_mode: bool = False) -> InlineKeyboardMarkup:
    back = _button(GUIDE_TO_QUESTIONS_BUTTON, "guide_screen", screen_id(START, text_mode=text_mode))
    return InlineKeyboardMarkup(inline_keyboard=[[back, _to_menu()]])


def guide_button(*, from_menu: bool = False) -> InlineKeyboardButton:
    """«📖 Как пользоваться» for any keyboard: sends the first screen as a new message (in place
    of the pressed one, `from_menu`)."""
    return _button(GUIDE_BUTTON, "guide", FROM_MENU if from_menu else "")


class Guide:
    def __init__(self, photos: AssetPhotos, bot: BotClient, *, trial: TrialLimits = _TRIAL) -> None:
        self._photos = photos
        self._bot = bot
        self._answers = build_answers(trial)
        self._by_key = {answer.key: answer for answer in self._answers}

    def _screen(self, key: str) -> tuple[Screen, InlineKeyboardMarkup, InlineKeyboardMarkup]:
        """The screen (the first one for an unknown key), its photo-mode keyboard and its
        text-mode one."""
        answer = self._by_key.get(key)
        if answer is None:
            return (
                START_SCREEN,
                build_start_keyboard(self._answers),
                build_start_keyboard(self._answers, text_mode=True),
            )
        return answer, build_answer_keyboard(), build_answer_keyboard(text_mode=True)

    async def send(self, chat_id: int) -> None:
        """The first screen as a new message."""
        screen, keyboard, text_keyboard = self._screen(START)
        await self._photos.send(
            chat_id, screen.picture, screen.caption, keyboard, text_markup=text_keyboard
        )

    async def show(self, chat_id: int, message_id: int, id_: str) -> None:
        """Switch the Инструкция `message_id` to the screen a `guide_screen` id names. A photo
        message swaps its picture and caption; one that went out as text (its buttons say so)
        is redrawn as text; one that can't be edited at all is sent anew."""
        key, text_mode = parse_screen_id(id_)
        screen, keyboard, text_keyboard = self._screen(key)
        if not text_mode and await self._photos.edit(
            chat_id, message_id, screen.picture, screen.caption, keyboard
        ):
            return
        try:
            await self._bot.edit_message_text(
                chat_id, message_id, screen.caption, reply_markup=text_keyboard
            )
            return
        except Exception:
            logger.info("could not switch guide message %s", message_id, exc_info=True)
        await self._photos.send(
            chat_id, screen.picture, screen.caption, keyboard, text_markup=text_keyboard
        )
