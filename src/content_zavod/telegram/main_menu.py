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

The menu's text (#149) sums up the Пространство: this week's Plan and how far its Статьи are
(from the Хаб's own `get_hub`), when the Расписание makes the next Plan (the tick's
`next_deadline`, in the Пространство's time zone), the Лимит, and the Ниша.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Protocol
from zoneinfo import ZoneInfo

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from ..scheduling import (
    DEFAULT_DAY_OF_WEEK,
    DEFAULT_HOUR,
    DEFAULT_MINUTE,
    DEFAULT_SCHEDULE,
    next_deadline,
)
from ..workspaces import Limits, Usage, Workspace, WorkspaceId
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
    MENU_NICHE,
    MENU_NICHE_UNSET,
    MENU_PLAN_BUTTON,
    MENU_SCHEDULE_BUTTON,
    MENU_SETTINGS_BUTTON,
    MENU_TITLE,
    MENU_TOPIC_BUTTON,
    MENU_WEEK,
    MENU_WEEK_ARTICLES,
    MENU_WEEK_MORE,
    MENU_WEEK_NONE,
    MENU_WEEK_PENDING,
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
    menu_limit_text,
    next_plan_text,
    schedule_text,
    topics_text,
)
from .topic_command import TOPIC_MAX_LENGTH, PlanProposal, handle_topic_command
from .types import PlanHubView, PlanItemView, PlanView

logger = logging.getLogger(__name__)

TOPIC_INPUT_KIND = "topic_input"
# The menu names at most this many of the week's Темы, each cut to this length (#149).
MENU_TOPICS_SHOWN = 3
MENU_TOPIC_MAX_LENGTH = 40


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


def _short_title(title: str) -> str:
    if len(title) <= MENU_TOPIC_MAX_LENGTH:
        return title
    return f"{title[: MENU_TOPIC_MAX_LENGTH - 1].rstrip()}…"


def _week_text(view: PlanView | None, hub: PlanHubView | None) -> str:
    """«📋 Эта неделя (…)», its Темы and Статьи; a Plan still waiting for approval in one line."""
    live = [] if view is None else _live_items(view)
    if not live:
        return MENU_WEEK_NONE
    if hub is None:
        return MENU_WEEK_PENDING.format(topics=topics_text(len(live)))
    lines = [MENU_WEEK.format(week=format_week_range(hub.week_label, year=False))]
    titles = [f"«{_short_title(topic.title)}»" for topic in hub.topics]
    if titles:
        more = len(titles) - MENU_TOPICS_SHOWN
        tail = MENU_WEEK_MORE.format(count=more) if more > 0 else ""
        lines.append(", ".join(titles[:MENU_TOPICS_SHOWN]) + tail)
    cells = [cell for topic in hub.topics for cell in topic.articles]
    if any(cell.article_id is not None for cell in cells):
        lines.append(
            MENU_WEEK_ARTICLES.format(
                ready=sum(cell.state == "ready" for cell in cells),
                total=len(cells),
                exported=sum(cell.exported for cell in cells),
            )
        )
    return "\n".join(lines)


def render_menu_text(
    *,
    view: PlanView | None,
    hub: PlanHubView | None,
    next_plan: datetime | None,
    usage: Usage,
    contact: str | None,
    niche: str | None,
) -> str:
    """The Главное меню's text (#149). `hub` - the week's Plan once approved; `next_plan` -
    `None` when the Расписание won't make one by itself; `niche` - `None` until it is told."""
    week = _week_text(view, hub)
    if next_plan is not None:
        week = f"{week}\n\n{next_plan_text(next_plan)}"
    niche_line = MENU_NICHE_UNSET if niche is None else MENU_NICHE.format(niche=niche)
    return "\n\n".join([MENU_TITLE, week, menu_limit_text(usage, contact), niche_line])


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
        limits: Limits,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        photos: AssetPhotos | None = None,
    ) -> None:
        """`settings` and `onboarding` say whether the Расписание will make the week's Plan by
        itself (a Ниша told, the wizard not mid-way - what the tick checks). `photos` - where the
        /start welcome picture comes from (#114); without it the welcome is text, as before."""
        self._settings = settings
        self._onboarding = onboarding
        self._limits = limits
        self._photos = photos
        self._plan = plan
        self._queue = queue
        self._schedule = schedule
        self._bot = bot
        self._gateway = gateway
        self._prompts = prompts
        self._now = now

    async def _text(self, workspace: Workspace) -> str:
        """Every read is by this Пространство's id; the Хаб and the Расписание only when the
        text needs them - the menu opens often."""
        now = self._now()
        view = await self._plan.find_active(workspace.id, workspace_week(workspace, now))
        live = [] if view is None else _live_items(view)
        hub = None
        if view is not None and live and all(item.status == "approved" for item in live):
            hub = await self._plan.get_hub(workspace.id, view.id)
        owner = await self._settings.read(workspace.id)
        usage = await self._limits.usage(workspace.id)
        next_plan = None
        # The tick's own conditions (#118): no Plan without a Ниша, mid-wizard, or with the
        # Лимит of Планов used up. Not mirrored: its `_week_requested` (the week's Job exists,
        # its Plan archived or empty) - a narrow case not worth one more query per open.
        if (
            usage.plans_left > 0
            and owner.niche_saved
            and not await self._onboarding.needed(workspace.id)
        ):
            schedule = await self._schedule.get(workspace.id) or DEFAULT_SCHEDULE
            next_plan = next_deadline(
                now, ZoneInfo(workspace.timezone), schedule, week_has_plan=view is not None
            )
        return render_menu_text(
            view=view,
            hub=hub,
            next_plan=next_plan,
            usage=usage,
            contact=self._limits.contact,
            niche=owner.niche if owner.niche_saved else None,
        )

    async def send(self, workspace: Workspace, chat_id: int, *, welcome: bool = False) -> None:
        """The menu as a new message. `welcome` (/start) puts the greeting above it - as its own
        picture when there are pictures (#114), since the menu itself is edited in place and a
        photo message can't turn into a text one."""
        text = await self._text(workspace)
        if welcome and self._photos is not None:
            await self._photos.send(chat_id, "welcome", WELCOME_TEXT)
        elif welcome:
            text = f"{WELCOME_TEXT}\n\n{text}"
        await self._bot.send_message(chat_id, text, reply_markup=build_main_menu_keyboard())

    async def show(self, workspace: Workspace, chat_id: int, message_id: int) -> None:
        """The menu in place of the pressed message - or, when that one can't become the menu
        (a photo: the onboarding intro), as a new message below it."""
        try:
            await self._bot.edit_message_text(
                chat_id,
                message_id,
                await self._text(workspace),
                reply_markup=build_main_menu_keyboard(),
            )
        except Exception:
            logger.info("could not turn message %s into the menu", message_id, exc_info=True)
            await self.send(workspace, chat_id)

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
            limits=self._limits,
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
