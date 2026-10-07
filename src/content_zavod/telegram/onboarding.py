"""Onboarding (#96): the Владелец's first-run wizard, in the spirit of «Постодел».

`/start` from a Владелец whose bot isn't set up yet (`OnboardingState.needed`) opens an intro
- what the bot does and what is about to happen - with «▶ Начать настройку» / «Позже». The
wizard then asks the Настройки one at a time, in `ONBOARDING_ORDER` (Ниша → Аудитория →
Персона → Направления → Проект), ends on a «🔎 Проверьте вводные» card with «🚀 Запустить» and
an «✏️ Изменить …» per step, and «🚀 Запустить» starts the first Plan at once through the same
path as /generate_plan and «🪄 Составить План».

Every step is a `SettingField` from `settings_screen.SETTING_FIELDS`: its Пресеты (Персона's
become buttons plus «✏️ Своя Персона»), its validation and its save are exactly the Экран
Настроек's. A key of `ONBOARDING_ORDER` with no such field is skipped, and a new field only needs
its key added there. The per-step extras are `ONBOARDING_STEPS`: may it be skipped, a note above
the question, and the first-run wording of the question - the Экран Настроек asks to *change* a
value («Напишите новую Нишу»), the wizard asks for it for the first time («Какая у вас Ниша?»).
A step without its own wording falls back to the field's `question`.

Each step is one `InputPrompt` question (the shared typed-input wait, #88) with «◀ Назад»,
«Пропустить ⏭» where the step is optional, and «Отмена»; an answer is saved straight away, so a
restart mid-wizard loses nothing, and the next screen is a new message at the bottom of the
chat while the previous one is deleted. Where a screen goes is carried in its buttons' callback
ids and its wait's target (`StepRef`), never in memory. Any command - /menu included - drops
the wizard's open question (`interrupt`), so the wizard never blocks the rest of the bot; /start
offers it again until it's finished.

The Направления step also offers «✨ Предложить по Нише» (#113): `DirectionSuggestions` picks
them from the Ниша and Аудитория and comes back here (`suggestion_*`) with the step's
`StepRef` as its origin `o:<StepRef>` - «✅ Взять» moves on like an answer, «✏️ Написать свои»
and «Отмена» reopen the step.

The wizard only runs in the private chat: a Владелец pressing /start in a group gets the usual
menu plus a pointer to the private chat.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Protocol

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from ..access import Role
from ..domain.errors import InvalidSettingValue
from ..scheduling import DEFAULT_DAY_OF_WEEK, DEFAULT_HOUR, DEFAULT_MINUTE
from ..settings import (
    PERSONAS,
    OwnerSettings,
    SettingsService,
    directions_mismatch,
)
from .callback_codec import Action, SimpleAction, encode_callback_data
from .gateway import BotClient
from .input_prompt import InputPrompt
from .main_menu import build_open_menu_keyboard
from .settings_screen import (
    SETTING_FIELDS,
    ScheduleStore,
    SettingField,
    current_line,
    suggest_rows,
)
from .texts import (
    BACK_BUTTON,
    ONBOARDING_ALREADY_LAUNCHED,
    ONBOARDING_AUDIENCE_QUESTION,
    ONBOARDING_CANCELLED,
    ONBOARDING_DIRECTIONS_MISMATCH,
    ONBOARDING_DIRECTIONS_QUESTION,
    ONBOARDING_IN_PRIVATE,
    ONBOARDING_INTRO,
    ONBOARDING_LATER_BUTTON,
    ONBOARDING_LAUNCH_BUTTON,
    ONBOARDING_LAUNCHED,
    ONBOARDING_NICHE_QUESTION,
    ONBOARDING_OPEN_PRIVATE_BUTTON,
    ONBOARDING_PERSONA_CHOOSE,
    ONBOARDING_PLAN_HERE,
    ONBOARDING_PLAN_IN_TEAM,
    ONBOARDING_PROJECT_QUESTION,
    ONBOARDING_REVIEW_DIRECTIONS_MISMATCH,
    ONBOARDING_REVIEW_HINT,
    ONBOARDING_REVIEW_TITLE,
    ONBOARDING_SKIP_BUTTON,
    ONBOARDING_START_BUTTON,
    ONBOARDING_STEP_HEADER,
    SETTINGS_CHOOSE,
    SETTINGS_SAVED,
    schedule_text,
    steps_count_text,
)

ONBOARDING_INPUT_KIND = "onboarding_input"
"""`PendingInputs.kind` of every wizard question; the target is the step's `StepRef`."""

ONBOARDING_ORDER: tuple[str, ...] = ("niche", "audience", "persona", "directions", "project")
"""The wizard's steps, by `SettingField.key`. Аудитория follows Ниша (the Владелец's decision on
#96), whatever its place on the Экран Настроек."""

# `onboarding_step` ids besides a `StepRef`.
START = "start"
INTRO = "intro"
REVIEW = "review"


@dataclass(frozen=True)
class OnboardingStep:
    """What a wizard step adds to its `SettingField`."""

    skippable: bool = True
    note: Callable[[OwnerSettings], str | None] = field(default=lambda _current: None)
    """A warning shown above the question, if it returns one."""
    question: str | None = None
    """The first-run question with an example, in place of the field's `question` (which asks
    for a *new* value). The «Своя …» variant of a step with Пресеты keeps the field's."""
    choose: str | None = None
    """For a step with Пресеты: the first-run text above the Пресет buttons."""


def _directions_note(current: OwnerSettings) -> str | None:
    if directions_mismatch(current):
        return ONBOARDING_DIRECTIONS_MISMATCH.format(niche=current.niche)
    return None


_PERSONA_CHOOSE = ONBOARDING_PERSONA_CHOOSE.format(
    presets="\n".join(f"• {persona.title} — {persona.role}" for persona in PERSONAS.values())
)

ONBOARDING_STEPS: Mapping[str, OnboardingStep] = {
    # The one answer the bot can't guess; everything else has a sensible default.
    "niche": OnboardingStep(skippable=False, question=ONBOARDING_NICHE_QUESTION),
    "audience": OnboardingStep(question=ONBOARDING_AUDIENCE_QUESTION),
    "persona": OnboardingStep(choose=_PERSONA_CHOOSE),
    "directions": OnboardingStep(note=_directions_note, question=ONBOARDING_DIRECTIONS_QUESTION),
    "project": OnboardingStep(question=ONBOARDING_PROJECT_QUESTION),
}
_DEFAULT_STEP = OnboardingStep()


def onboarding_fields(
    available: Sequence[SettingField] = SETTING_FIELDS,
) -> tuple[SettingField, ...]:
    """The wizard's steps: `ONBOARDING_ORDER`, minus any key `available` has no field for."""
    by_key = {setting.key: setting for setting in available}
    return tuple(by_key[key] for key in ONBOARDING_ORDER if key in by_key)


@dataclass(frozen=True)
class StepRef:
    """One wizard screen, as packed into callback ids and the wait's target: `key`, or
    `key:` + flags - `r` opened from «Проверьте вводные» (Назад/Пропустить/an answer return
    there), `c` the typed «Своя …» variant of a step with Пресеты."""

    key: str
    review: bool = False
    custom: bool = False

    def encode(self) -> str:
        flags = ("r" if self.review else "") + ("c" if self.custom else "")
        return f"{self.key}:{flags}" if flags else self.key

    @classmethod
    def decode(cls, text: str) -> StepRef:
        key, _, flags = text.partition(":")
        return cls(key, review="r" in flags, custom="c" in flags)


class MenuEntry(Protocol):
    """What the wizard needs from the Главное меню: show it, and start a Plan the way
    «🪄 Составить План» does."""

    async def show(self, chat_id: int, message_id: int, role: Role) -> None: ...

    async def generate_plan(self, chat_id: int, *, announce: bool = True) -> None: ...


class OnboardingFlag(Protocol):
    async def needed(self) -> bool: ...

    async def begin(self) -> None: ...

    async def finish(self) -> bool: ...

    async def skip(self) -> None: ...


def _button(text: str, action: Action, id_: str = "") -> InlineKeyboardButton:
    return InlineKeyboardButton(
        text=text, callback_data=encode_callback_data(SimpleAction(action, id_))
    )


def _is_private(chat_id: int, user_id: int) -> bool:
    return chat_id == user_id


def _with_notice(text: str, notice: str | None) -> str:
    return f"{SETTINGS_SAVED.format(text=notice)}\n\n{text}" if notice else text


def render_intro_text(fields: Sequence[SettingField]) -> str:
    return ONBOARDING_INTRO.format(
        count=steps_count_text(len(fields)),
        steps=" → ".join(setting.label for setting in fields),
    )


def build_intro_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_button(ONBOARDING_START_BUTTON, "onboarding_step", START)],
            [_button(ONBOARDING_LATER_BUTTON, "onboarding_later")],
        ]
    )


def render_review_text(
    fields: Sequence[SettingField], current: OwnerSettings, *, notice: str | None = None
) -> str:
    lines = "\n".join(f"{setting.label}: {setting.show(current)}" for setting in fields)
    blocks = [ONBOARDING_REVIEW_TITLE, lines]
    if any(setting.key == "directions" for setting in fields) and directions_mismatch(current):
        blocks.append(ONBOARDING_REVIEW_DIRECTIONS_MISMATCH.format(niche=current.niche))
    blocks.append(ONBOARDING_REVIEW_HINT)
    return _with_notice("\n\n".join(blocks), notice)


def build_review_keyboard(fields: Sequence[SettingField]) -> InlineKeyboardMarkup:
    rows = [[_button(ONBOARDING_LAUNCH_BUTTON, "onboarding_launch")]]
    rows += [
        [_button(setting.change_button, "onboarding_step", StepRef(setting.key, True).encode())]
        for setting in fields
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


class Onboarding:
    def __init__(
        self,
        state: OnboardingFlag,
        settings: SettingsService,
        prompts: InputPrompt,
        bot: BotClient,
        menu: MenuEntry,
        schedule: ScheduleStore,
        *,
        bot_username: str | None = None,
        team_chat_id: int | None = None,
        fields: Sequence[SettingField] | None = None,
    ) -> None:
        """`team_chat_id` - where Plans are delivered (`TELEGRAM_NOTIFY_CHAT_ID`): the launch
        says «в чат команды», or «сюда» when that is this very private chat."""
        self._state = state
        self._settings = settings
        self._prompts = prompts
        self._bot = bot
        self._menu = menu
        self._schedule = schedule
        self._bot_username = bot_username
        self._team_chat_id = team_chat_id
        self._fields = tuple(fields) if fields is not None else onboarding_fields()
        self._keys = [setting.key for setting in self._fields]

    # --- entry points ---

    async def offer(self, chat_id: int, user_id: int, role: Role | None) -> bool:
        """/start: the intro, if this is a Владелец whose bot still needs setting up - `True`
        when it took /start's place. In a group it only points to the private chat and
        returns `False`, so the group still gets its menu."""
        if role != "owner" or not await self._state.needed():
            return False
        if not _is_private(chat_id, user_id):
            await self._point_to_private(chat_id)
            return False
        await self._bot.send_message(
            chat_id, render_intro_text(self._fields), reply_markup=build_intro_keyboard()
        )
        return True

    async def interrupt(self, chat_id: int, user_id: int) -> None:
        """Any command: drop the wizard's open question, so the next message isn't taken for
        an answer. The wizard itself stays unfinished - /start offers it again."""
        await self._prompts.cancel(chat_id, user_id, ONBOARDING_INPUT_KIND)

    async def cancelled(self, chat_id: int) -> None:
        """After «Отмена» on a wizard question: how to come back."""
        await self._bot.send_message(
            chat_id, ONBOARDING_CANCELLED, reply_markup=build_open_menu_keyboard()
        )

    # --- buttons ---

    async def go(self, chat_id: int, user_id: int, message_id: int, step_id: str) -> None:
        """`onboarding_step`: «Начать настройку», «◀ Назад», «Пропустить ⏭», «✏️ Своя …» and
        «✏️ Изменить …» all just open another screen in place of the pressed one."""
        if not _is_private(chat_id, user_id):
            await self._point_to_private(chat_id)
            return
        if step_id == START and not await self._state.needed():
            # A stale intro, after «Позже» or a launch: it must not re-arm the wizard.
            await self._menu.show(chat_id, message_id, "owner")
            return
        await self._leave(chat_id, user_id, message_id)
        if step_id == START:
            await self._state.begin()
            step_id = self._keys[0] if self._keys else REVIEW
        await self._open(chat_id, user_id, step_id)

    async def pick(self, chat_id: int, user_id: int, message_id: int, pick_id: str) -> None:
        """`onboarding_pick`: a Пресет button - saved as if typed, then the next screen."""
        if not _is_private(chat_id, user_id):
            await self._point_to_private(chat_id)
            return
        ref_text, _, index_text = pick_id.rpartition(":")
        ref = StepRef.decode(ref_text)
        setting = self._field(ref.key)
        index = int(index_text) if index_text.isdigit() else -1
        if setting is None or not 0 <= index < len(setting.presets):
            await self.go(chat_id, user_id, message_id, ref.encode())
            return
        notice = await setting.save(self._settings, setting.presets[index][1])
        await self._leave(chat_id, user_id, message_id)
        await self._open(chat_id, user_id, self._after(ref), notice=notice)

    async def later(self, chat_id: int, user_id: int, message_id: int) -> None:
        """«Позже — открыть меню»: the wizard is not offered again; Настройки are in the menu."""
        await self._state.skip()
        await self._menu.show(chat_id, message_id, "owner")

    async def launch(self, chat_id: int, user_id: int, message_id: int) -> None:
        """«🚀 Запустить»: finish and start the first Plan. Only the press that actually
        finished the wizard starts it - a double tap, a stale card or a second Владелец
        launching too only hear it's already on its way."""
        if not _is_private(chat_id, user_id):
            await self._point_to_private(chat_id)
            return
        where = ONBOARDING_PLAN_HERE if chat_id == self._team_chat_id else ONBOARDING_PLAN_IN_TEAM
        if not await self._state.finish():
            await self._bot.edit_message_text(
                chat_id,
                message_id,
                ONBOARDING_ALREADY_LAUNCHED.format(where=where),
                reply_markup=build_open_menu_keyboard(),
            )
            return
        schedule = await self._schedule.get()
        when = (
            schedule_text(schedule.day_of_week, schedule.hour, schedule.minute)
            if schedule is not None
            else schedule_text(DEFAULT_DAY_OF_WEEK, DEFAULT_HOUR, DEFAULT_MINUTE)
        )
        await self._bot.edit_message_text(
            chat_id,
            message_id,
            ONBOARDING_LAUNCHED.format(where=where, schedule=when),
            reply_markup=build_open_menu_keyboard(),
        )
        # The card above already says what is happening and where the Plan will land.
        await self._menu.generate_plan(chat_id, announce=False)

    # --- Направления suggested from the Ниша (#113): the wizard as their origin ---

    async def suggestion_opened(
        self, chat_id: int, user_id: int, message_id: int, place: str
    ) -> None:
        """«✨» was pressed on the step's question: it goes, its wait with it."""
        await self._leave(chat_id, user_id, message_id)

    async def suggestion_taken(self, chat_id: int, user_id: int, place: str, notice: str) -> None:
        """Saved as if typed: on to the next step (or back to «Проверьте вводные»)."""
        await self._open(chat_id, user_id, self._after(StepRef.decode(place)), notice=notice)

    async def suggestion_declined(
        self, chat_id: int, user_id: int, place: str, *, write_own: bool
    ) -> None:
        """Both reopen the step: its question is where they are typed."""
        await self._open(chat_id, user_id, place)

    # --- typed answers ---

    async def handle_reply(
        self,
        chat_id: int,
        user_id: int,
        text: str,
        reply_to_message_id: int | None,
        *,
        role: Role | None,
    ) -> bool:
        """A message that may answer a wizard question; `False` if it doesn't."""
        pending = await self._prompts.take(
            chat_id, user_id, ONBOARDING_INPUT_KIND, reply_to_message_id
        )
        if pending is None:
            return False
        if role != "owner":  # no longer a Владелец since the question was asked
            await self._prompts.close(chat_id, pending)
            return True
        ref = StepRef.decode(pending.target_id)
        setting = self._field(ref.key)
        if setting is None:  # a step this build no longer has
            await self._prompts.close(chat_id, pending)
            await self._open(chat_id, user_id, REVIEW)
            return True
        try:
            notice = await setting.save(self._settings, text)
        except InvalidSettingValue as exc:
            problem = setting.invalid_by_field.get(exc.field, setting.invalid)
            question, buttons = await self._step_screen(ref)
            await self._prompts.ask_again(
                chat_id, user_id, pending, problem, question, buttons=buttons
            )
            return True
        await self._prompts.close(chat_id, pending)
        await self._open(chat_id, user_id, self._after(ref), notice=notice)
        return True

    # --- screens ---

    async def _open(
        self, chat_id: int, user_id: int, step_id: str, *, notice: str | None = None
    ) -> None:
        if step_id == INTRO:
            await self._bot.send_message(
                chat_id, render_intro_text(self._fields), reply_markup=build_intro_keyboard()
            )
            return
        ref = StepRef.decode(step_id)
        setting = self._field(ref.key)
        if step_id == REVIEW or setting is None:  # unknown: a button from another build
            current = await self._settings.read()
            await self._bot.send_message(
                chat_id,
                render_review_text(self._fields, current, notice=notice),
                reply_markup=build_review_keyboard(self._fields),
            )
            return
        question, buttons = await self._step_screen(ref, notice=notice)
        await self._prompts.ask(
            chat_id,
            user_id,
            ONBOARDING_INPUT_KIND,
            ref.encode(),
            question,
            placeholder=setting.placeholder,
            buttons=buttons,
        )

    async def _step_screen(
        self, ref: StepRef, *, notice: str | None = None
    ) -> tuple[str, list[list[InlineKeyboardButton]]]:
        setting = self._fields[self._keys.index(ref.key)]
        step = ONBOARDING_STEPS.get(ref.key, _DEFAULT_STEP)
        current = await self._settings.read()
        chooser = bool(setting.presets) and not ref.custom

        blocks = [
            ONBOARDING_STEP_HEADER.format(
                number=self._keys.index(ref.key) + 1,
                total=len(self._keys),
                label=setting.label,
                purpose=setting.purpose,
            )
        ]
        note = step.note(current)
        if note:
            blocks.append(note)
        if chooser:
            blocks.append(step.choose or SETTINGS_CHOOSE)
        elif ref.custom:
            blocks.append(setting.question)
        else:
            blocks.append(step.question or setting.question)
        blocks.append(current_line((setting.detail or setting.show)(current)))
        text = _with_notice("\n\n".join(blocks), notice)

        rows: list[list[InlineKeyboardButton]] = []
        if chooser:
            shown = setting.show(current)
            rows += [
                [
                    _button(
                        f"✅ {title}" if title == shown else title,
                        "onboarding_pick",
                        f"{ref.encode()}:{index}",
                    )
                ]
                for index, (title, _value) in enumerate(setting.presets)
            ]
            rows.append(
                [
                    _button(
                        setting.custom_button,
                        "onboarding_step",
                        StepRef(ref.key, ref.review, custom=True).encode(),
                    )
                ]
            )
        if not chooser:
            rows += suggest_rows(setting, f"o:{ref.encode()}")
        nav = [_button(BACK_BUTTON, "onboarding_step", self._before(ref))]
        if step.skippable and not ref.review:
            nav.append(_button(ONBOARDING_SKIP_BUTTON, "onboarding_step", self._after(ref)))
        rows.append(nav)
        return text, rows

    # --- helpers ---

    def _field(self, key: str) -> SettingField | None:
        return self._fields[self._keys.index(key)] if key in self._keys else None

    def _before(self, ref: StepRef) -> str:
        setting = self._field(ref.key)
        if ref.custom and setting is not None and setting.presets:
            return StepRef(ref.key, ref.review).encode()  # back to the Пресеты
        if ref.review:
            return REVIEW
        index = self._keys.index(ref.key)
        return self._keys[index - 1] if index > 0 else INTRO

    def _after(self, ref: StepRef) -> str:
        if ref.review or ref.key not in self._keys:
            return REVIEW
        index = self._keys.index(ref.key)
        return self._keys[index + 1] if index + 1 < len(self._keys) else REVIEW

    async def _leave(self, chat_id: int, user_id: int, message_id: int) -> None:
        """Remove the screen a button was pressed on: with its wait if it is the open question,
        else (the intro, the review card, a stale question) just the message."""
        if not await self._prompts.cancel(
            chat_id, user_id, ONBOARDING_INPUT_KIND, message_id=message_id
        ):
            await self._bot.delete_message(chat_id, message_id)

    async def _point_to_private(self, chat_id: int) -> None:
        keyboard = None
        if self._bot_username:
            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text=ONBOARDING_OPEN_PRIVATE_BUTTON,
                            url=f"https://t.me/{self._bot_username}?start=setup",
                        )
                    ]
                ]
            )
        await self._bot.send_message(chat_id, ONBOARDING_IN_PRIVATE, reply_markup=keyboard)
