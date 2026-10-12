"""Главное меню (#95): the one inline-keyboard message `/start` and `/menu` open, instead of a
command list.

Everyone gets the same buttons - there are no Roles, every user is the Владелец of their own
Пространство (ADR-0015): «📋 План недели», «✍️ Предложить Тему», «🗂 История», «⚙️ Настройки»,
«🕘 Расписание» and «📖 Как пользоваться», the Инструкция (#114).

«📋 План недели» says how far the week's Plan is and sends its message anew at the bottom of the
chat (a private chat has no link to an earlier message), or - with no Plan yet - says when the
next one is due and offers «🪄 Составить План» (the same path as /generate_plan). The week is
counted in the Пространство's own time zone, like its Расписание. «✍️ Предложить Тему» asks for
the Тема through `InputPrompt` and hands the answer to the same handler as /topic. «🗂 История»
turns the menu message into its screen, whose «🏠 В меню» row turns it back.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Protocol

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from ..scheduling import DEFAULT_DAY_OF_WEEK, DEFAULT_HOUR, DEFAULT_MINUTE
from ..workspaces import Workspace, WorkspaceId
from .asset_photos import AssetPhotos
from .callback_codec import Action, SimpleAction, encode_callback_data
from .gateway import BotClient, TelegramGateway, format_week_range
from .generate_plan_command import (
    GenerationJobs,
    NicheSource,
    PlanGeneration,
    handle_generate_plan_command,
    niche_button,
    workspace_week,
)
from .guide import guide_button
from .input_prompt import InputPrompt
from .plan_delivery import PlanResendSource, resend_plan_message
from .settings_screen import BACK_TO_MENU, ScheduleStore
from .texts import (
    BACK_BUTTON,
    MENU_HISTORY_BUTTON,
    MENU_PLAN_BUTTON,
    MENU_SCHEDULE_BUTTON,
    MENU_SETTINGS_BUTTON,
    MENU_TEXT,
    MENU_TOPIC_BUTTON,
    NICHE_MISSING_TEXT,
    OPEN_MENU_BUTTON,
    PLAN_EMPTY_TEXT,
    PLAN_GENERATE_BUTTON,
    PLAN_MISSING_MANUAL_TEXT,
    PLAN_MISSING_TEXT,
    PLAN_SENT_BELOW,
    PLAN_STATE_APPROVED,
    PLAN_STATE_PENDING,
    TOPIC_QUESTION,
    TOPIC_TOO_LONG_TEXT,
    WELCOME_TEXT,
    schedule_text,
)
from .topic_command import TOPIC_MAX_LENGTH, PlanProposal, handle_topic_command
from .types import PlanItemView, PlanView

logger = logging.getLogger(__name__)

TOPIC_INPUT_KIND = "topic_input"


class OnboardingNeed(Protocol):
    async def needed(self, workspace_id: WorkspaceId) -> bool: ...


class MenuPlan(PlanGeneration, PlanProposal, PlanResendSource, Protocol):
    """Plan operations the menu needs: finding the week's Plan and sending its message anew,
    generating one (/generate_plan's path) and adding a proposed Тема (/topic's path)."""


def _button(text: str, action: Action, id_: str = "") -> InlineKeyboardButton:
    return InlineKeyboardButton(
        text=text, callback_data=encode_callback_data(SimpleAction(action, id_))
    )


def build_main_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_button(MENU_PLAN_BUTTON, "menu_plan")],
            [_button(MENU_TOPIC_BUTTON, "menu_topic")],
            [_button(MENU_HISTORY_BUTTON, "menu_history")],
            [_button(MENU_SETTINGS_BUTTON, "settings")],
            [_button(MENU_SCHEDULE_BUTTON, "schedule", BACK_TO_MENU)],
            [guide_button(from_menu=True)],
        ]
    )


def build_open_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[_button(OPEN_MENU_BUTTON, "menu")]])


def _live_items(view: PlanView) -> list[PlanItemView]:
    return [item for item in view.items if item.status in ("pending_review", "approved")]


def render_plan_status_text(view: PlanView) -> str:
    live = _live_items(view)
    state = (
        PLAN_STATE_PENDING
        if any(item.status == "pending_review" for item in live)
        else PLAN_STATE_APPROVED
    )
    return (
        f"📋 План на {format_week_range(view.week_label)}\n"
        f"Тем: {len(live)} — {state}.\n\n{PLAN_SENT_BELOW}"
    )


class MainMenu:
    def __init__(
        self,
        plan: MenuPlan,
        queue: GenerationJobs,
        schedule: ScheduleStore,
        bot: BotClient,
        gateway: TelegramGateway,
        prompts: InputPrompt,
        *,
        settings: NicheSource,
        onboarding: OnboardingNeed,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        photos: AssetPhotos | None = None,
    ) -> None:
        """`settings` and `onboarding` say whether the Расписание will make the week's Plan by
        itself (a Ниша told, the wizard not mid-way - what the tick checks). `photos` - where the
        /start welcome picture comes from (#114); without it the welcome is text, as before."""
        self._settings = settings
        self._onboarding = onboarding
        self._photos = photos
        self._plan = plan
        self._queue = queue
        self._schedule = schedule
        self._bot = bot
        self._gateway = gateway
        self._prompts = prompts
        self._now = now

    async def send(self, chat_id: int, *, welcome: bool = False) -> None:
        """The menu as a new message. `welcome` (/start) puts the greeting above it - as its own
        picture when there are pictures (#114), since the menu itself is edited in place and a
        photo message can't turn into a text one."""
        text = MENU_TEXT
        if welcome and self._photos is not None:
            await self._photos.send(chat_id, "welcome", WELCOME_TEXT)
        elif welcome:
            text = f"{WELCOME_TEXT}\n\n{MENU_TEXT}"
        await self._bot.send_message(chat_id, text, reply_markup=build_main_menu_keyboard())

    async def show(self, chat_id: int, message_id: int) -> None:
        """The menu in place of the pressed message - or, when that one can't become the menu
        (a photo: the onboarding intro), as a new message below it."""
        try:
            await self._bot.edit_message_text(
                chat_id, message_id, MENU_TEXT, reply_markup=build_main_menu_keyboard()
            )
        except Exception:
            logger.info("could not turn message %s into the menu", message_id, exc_info=True)
            await self.send(chat_id)

    async def show_plan(self, workspace: Workspace, chat_id: int, message_id: int) -> None:
        week_label = workspace_week(workspace, self._now())
        view = await self._plan.find_active(workspace.id, week_label)
        back = [_button(BACK_BUTTON, "menu")]
        if view is None:
            text, rows = await self._missing_plan_screen(workspace, week_label)
            await self._bot.edit_message_text(
                chat_id,
                message_id,
                text,
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[*rows, back]),
            )
            return
        if not _live_items(view):
            # Every Тема was removed: nothing worth sending again.
            rows = [[_button(MENU_TOPIC_BUTTON, "menu_topic")], back]
            await self._bot.edit_message_text(
                chat_id,
                message_id,
                PLAN_EMPTY_TEXT.format(week=format_week_range(week_label)),
                reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
            )
            return
        await self._bot.edit_message_text(
            chat_id,
            message_id,
            render_plan_status_text(view),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[back]),
        )
        await resend_plan_message(self._plan, self._gateway, workspace.id, chat_id, view.id)

    async def _missing_plan_screen(
        self, workspace: Workspace, week_label: str
    ) -> tuple[str, list[list[InlineKeyboardButton]]]:
        """No Plan this week: what happens next. The Расписание only makes one once a Ниша is
        told and the wizard isn't mid-way - otherwise its time isn't promised."""
        if not (await self._settings.read(workspace.id)).niche_saved:
            return NICHE_MISSING_TEXT, [[niche_button()]]
        week = format_week_range(week_label)
        rows = [[_button(PLAN_GENERATE_BUTTON, "menu_generate_plan")]]
        if await self._onboarding.needed(workspace.id):
            return PLAN_MISSING_MANUAL_TEXT.format(week=week), rows
        schedule = await self._schedule.get(workspace.id)
        day, hour, minute = (
            (schedule.day_of_week, schedule.hour, schedule.minute)
            if schedule is not None
            else (DEFAULT_DAY_OF_WEEK, DEFAULT_HOUR, DEFAULT_MINUTE)
        )
        return PLAN_MISSING_TEXT.format(week=week, schedule=schedule_text(day, hour, minute)), rows

    async def generate_plan(
        self, workspace: Workspace, chat_id: int, *, announce: bool = True
    ) -> None:
        await handle_generate_plan_command(
            self._plan,
            self._gateway,
            workspace,
            chat_id,
            queue=self._queue,
            settings=self._settings,
            now=self._now,
            announce=announce,
        )

    async def ask_topic(self, workspace: Workspace, chat_id: int, user_id: int) -> None:
        await self._prompts.ask(
            workspace.id, chat_id, user_id, TOPIC_INPUT_KIND, "", TOPIC_QUESTION
        )

    async def handle_reply(
        self, workspace: Workspace, chat_id: int, user_id: int, text: str
    ) -> bool:
        """A message that may answer «✍️ Предложить Тему»; `False` if it doesn't."""
        pending = await self._prompts.take(workspace.id, chat_id, user_id, TOPIC_INPUT_KIND)
        if pending is None:
            return False
        if len(text.strip()) > TOPIC_MAX_LENGTH:
            # Refused before the wait is closed: the same question stays open (ADR-0015).
            problem = TOPIC_TOO_LONG_TEXT.format(limit=TOPIC_MAX_LENGTH)
            await self._prompts.ask_again(
                workspace.id, chat_id, user_id, pending, problem, TOPIC_QUESTION
            )
            return True
        await self._prompts.close(chat_id, pending)
        await handle_topic_command(
            self._plan, self._gateway, workspace, chat_id, text, now=self._now
        )
        return True
