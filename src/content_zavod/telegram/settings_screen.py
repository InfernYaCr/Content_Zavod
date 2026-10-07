"""Экран Настроек and the Расписание screen (#95): one message, edited in place, instead of the
ten `/niche`, `/set_niche`, … commands it replaces.

The screen lists every Настройка's current value with what it drives and an «Изменить …» button
per Настройка, plus the Расписание (not a Настройка - see CONTEXT.md - but the Owner looks for it
in the same place). «Изменить» asks for the new value with an example through `InputPrompt`
(the shared typed-input wait, #88); a Настройка with ready-made variants (Персона's Пресеты)
first turns the screen into a chooser - one button per variant plus «Своя». A saved value
redraws the same screen with «✅ … изменена» on top, so the Owner sees the result where they
pressed; a rejected one keeps the question open and says what was wrong.

Adding a Настройка is one `SettingField` entry in `SETTING_FIELDS`: its key, the words on the
screen, the question with an example, and how to save it through `SettingsService` - the
screen, its buttons, the prompt, the reply handling and the old-style `/set_<key>` alias all
read that list. Validation stays in `SettingsService`; an entry only phrases its
`InvalidSettingValue` for the Owner.

The Расписание screen has day-of-week buttons (save at once, keeping the time) and «Изменить
время» (a typed ЧЧ:ММ). Every change is persisted and the live APScheduler job rescheduled
immediately via its stable `JOB_ID`, as `/set_schedule` always did (and still does, as a
hidden alias). Its «◀ Назад» returns to wherever it was opened from - Главное меню or Настройки -
which rides along in its callback ids as `m`/`s`.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Protocol
from zoneinfo import ZoneInfo

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from apscheduler.triggers.cron import CronTrigger

from ..access import Role
from ..domain.errors import InvalidSettingValue
from ..scheduling import DEFAULT_DAY_OF_WEEK, DEFAULT_HOUR, DEFAULT_MINUTE, JOB_ID, ScheduleConfig
from ..settings import (
    AUDIENCE_MAX_LENGTH,
    PERSONAS,
    OwnerSettings,
    SettingsService,
    audience_detail_text,
    audience_screen_text,
    persona_detail_text,
    persona_display_title,
    persona_setting_value,
    project_detail_text,
    resolve_persona,
)
from .callback_codec import Action, SimpleAction, encode_callback_data
from .gateway import BotClient
from .input_prompt import InputPrompt
from .texts import (
    BACK_BUTTON,
    SCHEDULE_CHANGE_BUTTON,
    SCHEDULE_DAY_SAVED,
    SCHEDULE_HOW,
    SCHEDULE_LABEL,
    SCHEDULE_PURPOSE,
    SCHEDULE_TIME_BUTTON,
    SCHEDULE_TIME_INVALID,
    SCHEDULE_TIME_PLACEHOLDER,
    SCHEDULE_TIME_QUESTION,
    SCHEDULE_TIME_SAVED,
    SCHEDULE_TITLE,
    SETTINGS_CHOOSE,
    SETTINGS_CURRENT,
    SETTINGS_SAVED,
    SETTINGS_TITLE,
    WEEKDAYS,
    parse_weekday,
    schedule_text,
)

logger = logging.getLogger(__name__)

SETTING_INPUT_KIND = "setting_input"
"""`PendingInputs.kind` of every value this screen asks for; the target says which one."""

BACK_TO_MENU = "m"
BACK_TO_SETTINGS = "s"
_TIME_KEY = "time"


@dataclass(frozen=True)
class SettingField:
    """One Настройка on the screen. `key` goes into callback data and the pending-input
    target, so it stays short and has no «:»."""

    key: str
    label: str
    """As shown on the screen: «Ниша»."""
    change_button: str
    """«✏️ Изменить Нишу» - the accusative is why this isn't derived from `label`."""
    purpose: str
    """What the value drives, shown under it: «из неё подбираются Темы»."""
    show: Callable[[OwnerSettings], str]
    """The current value in one line, for the screen."""
    save: Callable[[SettingsService, str], Awaitable[str]]
    """Saves the Owner's text, returning the confirmation («Ниша изменена: …»); raises
    `InvalidSettingValue` (from `SettingsService`) without saving anything."""
    question: str
    """What to ask, with an example."""
    placeholder: str
    """The group ForceReply input hint (≤ 64 chars)."""
    invalid: str
    """Said when `save` rejects the text."""
    invalid_by_field: Mapping[str, str] = field(default_factory=dict)
    """A more precise `invalid`, by `InvalidSettingValue.field`."""
    detail: Callable[[OwnerSettings], str] | None = None
    """The full current value, if `show` shortens it (shown in the question and chooser)."""
    presets: tuple[tuple[str, str], ...] = ()
    """(button title, value passed to `save`): ready-made variants. Non-empty makes
    «Изменить» open a chooser with these plus `custom_button` instead of asking at once."""
    custom_button: str = "✏️ Свой вариант"


async def _save_niche(settings: SettingsService, text: str) -> str:
    return f"Ниша изменена: {await settings.set_niche(text)}"


async def _save_persona(settings: SettingsService, text: str) -> str:
    persona, custom_persona = resolve_persona(await settings.set_persona(text))
    return f"Персона изменена: {persona_display_title(persona, custom_persona)}"


async def _save_audience(settings: SettingsService, text: str) -> str:
    audience = await settings.set_audience(text)
    if audience is None:
        return "Аудитория убрана: Темы и Статьи будут без портрета читателя."
    return f"Аудитория изменена: {audience_screen_text(audience)}"


async def _save_directions(settings: SettingsService, text: str) -> str:
    return f"Направления изменены: {', '.join(await settings.set_directions(text))}"


async def _save_project(settings: SettingsService, text: str) -> str:
    project = await settings.set_project(text)
    if project is None:
        return "Проект убран: Статьи будут без ссылки в конце."
    return f"Проект изменён: {project_detail_text(project)}"


SETTING_FIELDS: tuple[SettingField, ...] = (
    SettingField(
        key="niche",
        label="Ниша",
        change_button="✏️ Изменить Нишу",
        purpose="из неё подбираются Темы",
        show=lambda current: current.niche,
        save=_save_niche,
        question=(
            "✏️ Напишите новую Нишу — тематику, в которой я подбираю Темы и пишу Статьи.\n\n"
            "Например: фитнес и здоровое питание"
        ),
        placeholder="Например: фитнес и здоровое питание",
        invalid="Ниша не может быть пустой.",
    ),
    SettingField(
        key="persona",
        label="Персона",
        change_button="✏️ Изменить Персону",
        purpose="от чьего лица пишутся Статьи",
        show=lambda current: persona_display_title(current.persona, current.custom_persona),
        detail=lambda current: persona_detail_text(current.persona, current.custom_persona),
        save=_save_persona,
        question=(
            "✏️ Опишите свою Персону: каждое поле с новой строки, обязательна только «Роль».\n\n"
            "Например:\n"
            "Название: Технооптимист\n"
            "Роль: фаундер, который сам внедряет AI в процессы\n"
            "Тон: энергичный, без канцелярита\n"
            "Запрещено: выдуманные кейсы"
        ),
        placeholder="Роль: …",
        invalid="Не нашёл строку «Роль: …» — без неё Персону не сохранить.",
        presets=tuple(
            (persona.title, persona_setting_value(persona.key)) for persona in PERSONAS.values()
        ),
        custom_button="✏️ Своя Персона",
    ),
    SettingField(
        key="audience",
        label="Аудитория",
        change_button="✏️ Изменить Аудиторию",
        purpose="для кого пишутся Статьи и подбираются Темы",
        show=lambda current: audience_screen_text(current.audience),
        detail=lambda current: audience_detail_text(current.audience),
        save=_save_audience,
        question=(
            "✏️ Опишите вашего читателя своими словами: кто он, что его беспокоит, чего он "
            "хочет добиться и насколько разбирается в теме. Под него я буду подбирать Темы "
            "и писать Статьи.\n\n"
            "Например: владельцы небольших кофеен и пекарен, 30–45 лет. Маркетингом "
            "занимаются сами, по вечерам. Боятся слить деньги на рекламу, не понимают, "
            "что работает. Хотят стабильный поток гостей без агентства. В терминах "
            "не разбираются — нужны простые шаги и примеры.\n\n"
            "Чтобы убрать Аудиторию, отправьте «-»."
        ),
        placeholder="Кто читатель, его боли, цели, уровень",
        invalid="Аудитория не может быть пустой — опишите читателя хотя бы одной фразой.",
        invalid_by_field={
            "audience_too_long": (
                f"Слишком длинно — уложитесь в {AUDIENCE_MAX_LENGTH} символов: "
                "главное о читателе, без лишних подробностей."
            )
        },
    ),
    SettingField(
        key="directions",
        label="Направления",
        change_button="✏️ Изменить Направления",
        purpose="запросы, по которым я ищу растущие Темы в Wordstat",
        show=lambda current: ", ".join(current.directions),
        save=_save_directions,
        question=(
            "✏️ Напишите Направления через запятую — по ним я ищу в Wordstat растущие запросы "
            "для Тем. Список заменится целиком.\n\n"
            "Например: фитнес дома, правильное питание, похудение после родов"
        ),
        placeholder="Через запятую: запрос 1, запрос 2",
        invalid="Нужно хотя бы одно Направление — пишите через запятую.",
    ),
    SettingField(
        key="project",
        label="Проект",
        change_button="✏️ Изменить Проект",
        purpose="ваш канал или сайт — ссылка на него в конце каждой Статьи",
        show=lambda current: project_detail_text(current.project),
        save=_save_project,
        question=(
            "✏️ Пришлите ссылку на ваш канал или сайт и через пробел — что там. "
            "Эта ссылка будет в конце каждой Статьи.\n\n"
            "Например: @my_channel Канал о маркетинге для малого бизнеса\n\n"
            "Чтобы убрать Проект, отправьте «-»."
        ),
        placeholder="@канал Описание канала",
        invalid="Нужны ссылка и описание через пробел, например: @my_channel Канал о маркетинге",
        invalid_by_field={
            "project_url": "Ссылка не распознана. Подойдёт @канал, t.me/канал или https://сайт."
        },
    ),
)
_FIELDS: dict[str, SettingField] = {setting.key: setting for setting in SETTING_FIELDS}

_TIME_RE = re.compile(r"^([01]?\d|2[0-3])(?:\s*[:.\- ]\s*([0-5]\d))?$")


def parse_time(text: str) -> tuple[int, int] | None:
    """«09:30», «9.30», «9 30», «9» -> (hour, minute); `None` for anything else. A phone's
    keyboard hides «:» a layer deeper than «.», so both count."""
    match = _TIME_RE.match(text.strip())
    if match is None:
        return None
    return int(match.group(1)), int(match.group(2) or 0)


class ScheduleStore(Protocol):
    async def get(self) -> ScheduleConfig | None: ...

    async def set(self, day_of_week: str, hour: int, minute: int) -> None: ...


class Rescheduler(Protocol):
    def reschedule_job(self, job_id: str, *, trigger: CronTrigger) -> None: ...


def _button(text: str, action: Action, id_: str = "") -> InlineKeyboardButton:
    return InlineKeyboardButton(
        text=text,
        callback_data=encode_callback_data(SimpleAction(action, id_)),
    )


def _with_notice(text: str, notice: str | None) -> str:
    return f"{SETTINGS_SAVED.format(text=notice)}\n\n{text}" if notice else text


def current_line(value: str) -> str:
    return SETTINGS_CURRENT.format(value=f"\n{value}" if "\n" in value else value)


def render_settings_text(
    current: OwnerSettings, schedule: ScheduleConfig, *, notice: str | None = None
) -> str:
    blocks = [SETTINGS_TITLE]
    for setting in SETTING_FIELDS:
        blocks.append(f"{setting.label}: {setting.show(current)}\n↳ {setting.purpose}")
    blocks.append(
        f"{SCHEDULE_LABEL}: "
        f"{schedule_text(schedule.day_of_week, schedule.hour, schedule.minute)}\n"
        f"↳ {SCHEDULE_PURPOSE}"
    )
    return _with_notice("\n\n".join(blocks), notice)


def build_settings_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [_button(setting.change_button, "edit_setting", setting.key)] for setting in SETTING_FIELDS
    ]
    rows.append([_button(SCHEDULE_CHANGE_BUTTON, "schedule", BACK_TO_SETTINGS)])
    rows.append([_button(BACK_BUTTON, "menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def render_chooser_text(setting: SettingField, current: OwnerSettings) -> str:
    value = (setting.detail or setting.show)(current)
    return f"{setting.label} — {setting.purpose}.\n\n{current_line(value)}\n\n{SETTINGS_CHOOSE}"


def build_chooser_keyboard(setting: SettingField, current: OwnerSettings) -> InlineKeyboardMarkup:
    shown = setting.show(current)
    rows = [
        [
            _button(
                f"✅ {title}" if title == shown else title,
                "pick_setting",
                f"{setting.key}:{index}",
            )
        ]
        for index, (title, _value) in enumerate(setting.presets)
    ]
    rows.append([_button(setting.custom_button, "ask_setting", setting.key)])
    rows.append([_button(BACK_BUTTON, "settings")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def render_schedule_text(schedule: ScheduleConfig, *, notice: str | None = None) -> str:
    now = schedule_text(schedule.day_of_week, schedule.hour, schedule.minute)
    return _with_notice(f"{SCHEDULE_TITLE}\n\n{current_line(now)}\n\n{SCHEDULE_HOW}", notice)


def build_schedule_keyboard(schedule: ScheduleConfig, back: str) -> InlineKeyboardMarkup:
    days = [
        _button(
            f"✅ {short}" if code == schedule.day_of_week else short,
            "schedule_day",
            f"{code}:{back}",
        )
        for code, (short, _full) in WEEKDAYS.items()
    ]
    back_button = (
        _button(BACK_BUTTON, "settings")
        if back == BACK_TO_SETTINGS
        else _button(BACK_BUTTON, "menu")
    )
    return InlineKeyboardMarkup(
        inline_keyboard=[
            days[:4],
            days[4:],
            [_button(SCHEDULE_TIME_BUTTON, "schedule_time", back)],
            [back_button],
        ]
    )


class SettingsScreen:
    def __init__(
        self,
        settings: SettingsService,
        schedule: ScheduleStore,
        scheduler: Rescheduler,
        bot: BotClient,
        prompts: InputPrompt,
        *,
        tz: ZoneInfo,
    ) -> None:
        self._settings = settings
        self._schedule = schedule
        self._scheduler = scheduler
        self._bot = bot
        self._prompts = prompts
        self._tz = tz

    # --- Настройки ---

    async def send(self, chat_id: int, *, notice: str | None = None) -> None:
        text = await self._settings_text(notice)
        await self._bot.send_message(chat_id, text, reply_markup=build_settings_keyboard())

    async def show(self, chat_id: int, message_id: int, *, notice: str | None = None) -> None:
        await self._redraw(
            chat_id, message_id, await self._settings_text(notice), build_settings_keyboard()
        )

    async def edit(self, chat_id: int, user_id: int, message_id: int, key: str) -> None:
        """«Изменить …»: the chooser for a Настройка with ready-made variants, else the question."""
        setting = _FIELDS.get(key)
        if setting is None:  # a button from a build that had a Настройка this one hasn't
            await self.show(chat_id, message_id)
            return
        if not setting.presets:
            await self.ask(chat_id, user_id, key, screen_message_id=message_id)
            return
        current = await self._settings.read()
        await self._redraw(
            chat_id,
            message_id,
            render_chooser_text(setting, current),
            build_chooser_keyboard(setting, current),
        )

    async def ask(
        self, chat_id: int, user_id: int, key: str, *, screen_message_id: int | None
    ) -> None:
        setting = _FIELDS.get(key)
        if setting is None:
            return
        await self._prompts.ask(
            chat_id,
            user_id,
            SETTING_INPUT_KIND,
            f"{key}:{screen_message_id or 0}",
            await self._question(setting),
            placeholder=setting.placeholder,
        )

    async def pick(self, chat_id: int, message_id: int, key: str, index: int) -> None:
        """A ready-made variant from the chooser: saved as if typed, then back to the screen."""
        setting = _FIELDS.get(key)
        if setting is None or not 0 <= index < len(setting.presets):
            await self.show(chat_id, message_id)
            return
        _title, value = setting.presets[index]
        notice = await setting.save(self._settings, value)
        await self.show(chat_id, message_id, notice=notice)

    async def apply_command(self, chat_id: int, user_id: int, key: str, args: str) -> None:
        """The hidden `/set_<key> <value>` aliases: save and show the screen; with no value,
        ask for it the same way «Изменить» does."""
        setting = _FIELDS[key]
        if not args.strip():
            await self.ask(chat_id, user_id, key, screen_message_id=None)
            return
        try:
            notice = await setting.save(self._settings, args)
        except InvalidSettingValue as exc:
            problem = setting.invalid_by_field.get(exc.field, setting.invalid)
            await self._bot.send_message(chat_id, f"⚠️ {problem}\n\n{setting.question}")
            return
        await self.send(chat_id, notice=notice)

    # --- Расписание ---

    async def send_schedule(self, chat_id: int, *, back: str = BACK_TO_MENU) -> None:
        schedule = await self._schedule_config()
        await self._bot.send_message(
            chat_id,
            render_schedule_text(schedule),
            reply_markup=build_schedule_keyboard(schedule, back),
        )

    async def show_schedule(
        self, chat_id: int, message_id: int, back: str, *, notice: str | None = None
    ) -> None:
        schedule = await self._schedule_config()
        await self._redraw(
            chat_id,
            message_id,
            render_schedule_text(schedule, notice=notice),
            build_schedule_keyboard(schedule, back),
        )

    async def set_day(self, chat_id: int, message_id: int, day: str, back: str) -> None:
        if day not in WEEKDAYS:
            await self.show_schedule(chat_id, message_id, back)
            return
        current = await self._schedule_config()
        saved = await self._reschedule(day, current.hour, current.minute)
        await self.show_schedule(
            chat_id, message_id, back, notice=SCHEDULE_DAY_SAVED.format(schedule=saved)
        )

    async def ask_time(self, chat_id: int, user_id: int, message_id: int, back: str) -> None:
        await self._prompts.ask(
            chat_id,
            user_id,
            SETTING_INPUT_KIND,
            f"{_TIME_KEY}:{message_id}:{back}",
            await self._time_question(),
            placeholder=SCHEDULE_TIME_PLACEHOLDER,
        )

    async def set_schedule_command(self, chat_id: int, args: str) -> None:
        """The hidden `/set_schedule <день> <ЧЧ:ММ>` alias, same replies as before #95."""
        parts = args.split()
        if len(parts) != 2:
            await self._bot.send_message(
                chat_id, "Использование: /set_schedule <день> <ЧЧ:ММ>, например: пн 09:00"
            )
            return
        day_text, time_text = parts
        day = parse_weekday(day_text)
        if day is None:
            valid = ", ".join(short for short, _ in WEEKDAYS.values())
            await self._bot.send_message(
                chat_id, f"Неизвестный день «{day_text}». Допустимые: {valid}"
            )
            return
        parsed = parse_time(time_text)
        if parsed is None:
            await self._bot.send_message(
                chat_id, f"Неверный формат времени «{time_text}». Нужно ЧЧ:ММ, например 09:00"
            )
            return
        saved = await self._reschedule(day, *parsed)
        await self._bot.send_message(chat_id, f"Расписание изменено: {saved}")

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
        """A message that may answer one of this screen's questions; `False` if it doesn't."""
        pending = await self._prompts.take(
            chat_id, user_id, SETTING_INPUT_KIND, reply_to_message_id
        )
        if pending is None:
            return False
        if role != "owner":  # no longer a Владелец since the question was asked
            await self._prompts.close(chat_id, pending)
            return True
        key, _, rest = pending.target_id.partition(":")
        message_id_text, _, back = rest.partition(":")
        screen_message_id = int(message_id_text or 0) or None

        if key == _TIME_KEY:
            parsed = parse_time(text)
            if parsed is None:
                problem = SCHEDULE_TIME_INVALID.format(text=text.strip()[:40])
                await self._prompts.ask_again(
                    chat_id, user_id, pending, problem, await self._time_question()
                )
                return True
            current = await self._schedule_config()
            saved = await self._reschedule(current.day_of_week, *parsed)
            await self._prompts.close(chat_id, pending)
            notice = SCHEDULE_TIME_SAVED.format(schedule=saved)
            schedule = await self._schedule_config()
            await self._redraw(
                chat_id,
                screen_message_id,
                render_schedule_text(schedule, notice=notice),
                build_schedule_keyboard(schedule, back or BACK_TO_MENU),
            )
            return True

        setting = _FIELDS.get(key)
        if setting is None:
            await self._prompts.close(chat_id, pending)
            return True
        try:
            notice = await setting.save(self._settings, text)
        except InvalidSettingValue as exc:
            problem = setting.invalid_by_field.get(exc.field, setting.invalid)
            await self._prompts.ask_again(
                chat_id, user_id, pending, problem, await self._question(setting)
            )
            return True
        await self._prompts.close(chat_id, pending)
        await self._redraw(
            chat_id,
            screen_message_id,
            await self._settings_text(notice),
            build_settings_keyboard(),
        )
        return True

    # --- helpers ---

    async def _settings_text(self, notice: str | None) -> str:
        return render_settings_text(
            await self._settings.read(), await self._schedule_config(), notice=notice
        )

    async def _question(self, setting: SettingField) -> str:
        current = await self._settings.read()
        value = (setting.detail or setting.show)(current)
        return f"{setting.question}\n\n{current_line(value)}"

    async def _time_question(self) -> str:
        schedule = await self._schedule_config()
        now = schedule_text(schedule.day_of_week, schedule.hour, schedule.minute)
        return f"{SCHEDULE_TIME_QUESTION}\n\n{current_line(now)}"

    async def _schedule_config(self) -> ScheduleConfig:
        return await self._schedule.get() or ScheduleConfig(
            day_of_week=DEFAULT_DAY_OF_WEEK, hour=DEFAULT_HOUR, minute=DEFAULT_MINUTE
        )

    async def _reschedule(self, day: str, hour: int, minute: int) -> str:
        await self._schedule.set(day, hour, minute)
        self._scheduler.reschedule_job(
            JOB_ID,
            trigger=CronTrigger(day_of_week=day, hour=hour, minute=minute, timezone=self._tz),
        )
        return schedule_text(day, hour, minute)

    async def _redraw(
        self,
        chat_id: int,
        message_id: int | None,
        text: str,
        keyboard: InlineKeyboardMarkup,
    ) -> None:
        """Edit the screen in place; send it anew when there is none to edit (a `/set_…`
        alias) or it can't be edited any more (deleted, too old)."""
        if message_id is not None:
            try:
                await self._bot.edit_message_text(chat_id, message_id, text, reply_markup=keyboard)
                return
            except Exception:
                logger.info(
                    "could not edit screen %s in chat %s", message_id, chat_id, exc_info=True
                )
        await self._bot.send_message(chat_id, text, reply_markup=keyboard)
