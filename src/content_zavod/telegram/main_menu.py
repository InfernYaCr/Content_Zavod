"""Главное меню (#95): the one inline-keyboard message `/start` and `/menu` open, instead of a
command list.

Every Role gets «📋 План недели», «✍️ Предложить Тему» and «🗂 История»; a Владелец also gets
«⚙️ Настройки», «👥 Участники» and «🕘 Расписание». Which buttons are shown is only a convenience
- each button's Действие is still gated by `ACTION_ROLE` in the callback dispatcher.

«📋 План недели» doesn't copy the Plan (it lives as one canonical message in the team chat,
ADR-0005): it says how far the week's Plan is and links to that message, or - with no Plan yet -
says when the next one is due and offers «🪄 Составить План» (the same path as /generate_plan).
«✍️ Предложить Тему» asks for the Тема through `InputPrompt` and hands the answer to the same
handler as /topic. «🗂 История» and «👥 Участники» turn the menu message into their existing
screens, whose «🏠 В меню» row turns it back.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Protocol
from zoneinfo import ZoneInfo

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from ..access import Role
from ..scheduling import DEFAULT_DAY_OF_WEEK, DEFAULT_HOUR, DEFAULT_MINUTE, week_label_for
from .callback_codec import Action, SimpleAction, encode_callback_data
from .gateway import BotClient, TelegramGateway, format_week_range
from .generate_plan_command import GenerationJobs, PlanGeneration, handle_generate_plan_command
from .input_prompt import InputPrompt
from .settings_screen import BACK_TO_MENU, ScheduleStore
from .texts import (
    BACK_BUTTON,
    HELP_OWNER_TEXT,
    HELP_TEXT,
    MENU_HISTORY_BUTTON,
    MENU_MEMBERS_BUTTON,
    MENU_PLAN_BUTTON,
    MENU_SCHEDULE_BUTTON,
    MENU_SETTINGS_BUTTON,
    MENU_TEXT,
    MENU_TOPIC_BUTTON,
    OPEN_MENU_BUTTON,
    PLAN_GENERATE_BUTTON,
    PLAN_IN_TEAM_CHAT,
    PLAN_IN_TEAM_CHAT_NO_LINK,
    PLAN_MISSING_TEXT,
    PLAN_OPEN_BUTTON,
    PLAN_STATE_APPROVED,
    PLAN_STATE_PENDING,
    TOPIC_PLACEHOLDER,
    TOPIC_QUESTION,
    WELCOME_TEXT,
    schedule_text,
)
from .topic_command import PlanProposal, handle_topic_command
from .types import PlanMessageRef, PlanView

TOPIC_INPUT_KIND = "topic_input"


class MenuPlan(PlanGeneration, PlanProposal, Protocol):
    """Plan operations the menu needs: finding the week's Plan and its message, generating
    one (/generate_plan's path) and adding a proposed Тема (/topic's path)."""


def _button(text: str, action: Action, id_: str = "") -> InlineKeyboardButton:
    return InlineKeyboardButton(
        text=text, callback_data=encode_callback_data(SimpleAction(action, id_))
    )


def build_main_menu_keyboard(role: Role) -> InlineKeyboardMarkup:
    rows = [
        [_button(MENU_PLAN_BUTTON, "menu_plan")],
        [_button(MENU_TOPIC_BUTTON, "menu_topic")],
        [_button(MENU_HISTORY_BUTTON, "menu_history")],
    ]
    if role == "owner":
        rows += [
            [_button(MENU_SETTINGS_BUTTON, "settings")],
            [_button(MENU_MEMBERS_BUTTON, "menu_members")],
            [_button(MENU_SCHEDULE_BUTTON, "schedule", BACK_TO_MENU)],
        ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_open_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[_button(OPEN_MENU_BUTTON, "menu")]])


def render_help_text(role: Role) -> str:
    return HELP_TEXT + (HELP_OWNER_TEXT if role == "owner" else "")


def plan_message_link(ref: PlanMessageRef) -> str | None:
    """`t.me/c/…` link to a message - only a supergroup's (`-100…` id) messages have one."""
    chat = str(ref.chat_id)
    if not chat.startswith("-100"):
        return None
    return f"https://t.me/c/{chat[4:]}/{ref.message_id}"


def render_plan_status_text(view: PlanView, *, linked: bool) -> str:
    live = [item for item in view.items if item.status in ("pending_review", "approved")]
    state = (
        PLAN_STATE_PENDING
        if any(item.status == "pending_review" for item in live)
        else PLAN_STATE_APPROVED
    )
    return (
        f"📋 План на {format_week_range(view.week_label)}\n"
        f"Тем: {len(live)} — {state}.\n\n"
        f"{PLAN_IN_TEAM_CHAT if linked else PLAN_IN_TEAM_CHAT_NO_LINK}"
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
        team_chat_id: int,
        tz: ZoneInfo,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._plan = plan
        self._queue = queue
        self._schedule = schedule
        self._bot = bot
        self._gateway = gateway
        self._prompts = prompts
        self._team_chat_id = team_chat_id
        self._tz = tz
        self._now = now

    async def send(self, chat_id: int, role: Role, *, welcome: bool = False) -> None:
        text = f"{WELCOME_TEXT}\n\n{MENU_TEXT}" if welcome else MENU_TEXT
        await self._bot.send_message(chat_id, text, reply_markup=build_main_menu_keyboard(role))

    async def show(self, chat_id: int, message_id: int, role: Role) -> None:
        await self._bot.edit_message_text(
            chat_id, message_id, MENU_TEXT, reply_markup=build_main_menu_keyboard(role)
        )

    async def show_plan(self, chat_id: int, message_id: int) -> None:
        week_label = week_label_for(self._now(), self._tz)
        view = await self._plan.find_active(week_label)
        if view is None:
            schedule = await self._schedule.get()
            day, hour, minute = (
                (schedule.day_of_week, schedule.hour, schedule.minute)
                if schedule is not None
                else (DEFAULT_DAY_OF_WEEK, DEFAULT_HOUR, DEFAULT_MINUTE)
            )
            text = PLAN_MISSING_TEXT.format(
                week=format_week_range(week_label), schedule=schedule_text(day, hour, minute)
            )
            rows = [[_button(PLAN_GENERATE_BUTTON, "menu_generate_plan")]]
        else:
            ref = await self._plan.get_message_ref(view.id)
            link = plan_message_link(ref) if ref is not None else None
            text = render_plan_status_text(view, linked=link is not None)
            rows = [[InlineKeyboardButton(text=PLAN_OPEN_BUTTON, url=link)]] if link else []
        rows.append([_button(BACK_BUTTON, "menu")])
        await self._bot.edit_message_text(
            chat_id, message_id, text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows)
        )

    async def generate_plan(self, chat_id: int, *, announce: bool = True) -> None:
        await handle_generate_plan_command(
            self._plan,
            self._gateway,
            chat_id,
            queue=self._queue,
            team_chat_id=self._team_chat_id,
            tz=self._tz,
            now=self._now,
            announce=announce,
        )

    async def ask_topic(self, chat_id: int, user_id: int) -> None:
        await self._prompts.ask(
            chat_id, user_id, TOPIC_INPUT_KIND, "", TOPIC_QUESTION, placeholder=TOPIC_PLACEHOLDER
        )

    async def handle_reply(
        self, chat_id: int, user_id: int, text: str, reply_to_message_id: int | None
    ) -> bool:
        """A message that may answer «✍️ Предложить Тему»; `False` if it doesn't."""
        pending = await self._prompts.take(chat_id, user_id, TOPIC_INPUT_KIND, reply_to_message_id)
        if pending is None:
            return False
        await self._prompts.close(chat_id, pending)
        await handle_topic_command(
            self._plan,
            self._gateway,
            chat_id,
            text,
            team_chat_id=self._team_chat_id,
            tz=self._tz,
            now=self._now,
        )
        return True
