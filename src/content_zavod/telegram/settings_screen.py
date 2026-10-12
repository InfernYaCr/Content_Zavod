"""Экран Настроек and the Расписание screen (#95): one message, edited in place, instead of the
ten `/niche`, `/set_niche`, … commands it replaces.

The screen lists every Настройка's current value with what it drives and an «Изменить …» button
per Настройка, plus the Расписание (not a Настройка - see CONTEXT.md - but the Owner looks for it
in the same place). «Изменить» asks for the new value with an example through `InputPrompt`
(the shared typed-input wait, #88); a Настройка with ready-made variants (Пресеты of the Стиль
автора) first turns the screen into a chooser - one button per variant plus «Свой». A saved value
redraws the same screen with «✅ … изменена» on top, so the Owner sees the result where they
pressed; a rejected one keeps the question open and says what was wrong.

Adding a Настройка is one `SettingField` entry in `SETTING_FIELDS`: its key, the words on the
screen, the question with an example, and how to save it through `SettingsService` - the
screen, its buttons, the prompt, the reply handling and the old-style `/set_<key>` alias all
read that list. Validation stays in `SettingsService`; an entry only phrases its
`InvalidSettingValue` for the Owner. On screen Персона is «Стиль автора» and Проект is «Ваш канал
или сайт» (#133, #134); the keys (`persona`, `project`) stay as they are.

Every screen acts in the Пространство of the update (ADR-0015): its Настройки, its Расписание
and its time zone, passed in as a `Workspace`.

The Расписание screen has day-of-week buttons (save at once, keeping the time), «Изменить
время» (a typed ЧЧ:ММ) and «Часовой пояс» (a chooser of Russia's zones, МСК−1 … МСК+9). A
change is only a write: the Расписание tick reads it from the database the next minute
(ADR-0015), so nothing in memory is rescheduled. `/set_schedule` stays as a hidden alias. Its
«◀ Назад» returns to wherever it was opened from - Главное меню or Настройки - which rides
along in its callback ids as `m`/`s`.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field, replace
from typing import Protocol

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from ..domain.errors import InvalidSettingValue
from ..scheduling import DEFAULT_SCHEDULE, ScheduleConfig
from ..settings import (
    AUDIENCE_MAX_LENGTH,
    NICHE_MAX_LENGTH,
    PERSONA_MAX_LENGTH,
    PERSONAS,
    PROJECT_MAX_LENGTH,
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
from ..workspaces import Workspace, WorkspaceId
from .callback_codec import Action, SimpleAction, encode_callback_data
from .gateway import BotClient
from .input_prompt import InputPrompt
from .texts import (
    BACK_BUTTON,
    NICHE_UNSET,
    PROJECT_EXAMPLES,
    SCHEDULE_CHANGE_BUTTON,
    SCHEDULE_DAY_SAVED,
    SCHEDULE_HOW,
    SCHEDULE_LABEL,
    SCHEDULE_PURPOSE,
    SCHEDULE_TIME_BUTTON,
    SCHEDULE_TIME_INVALID,
    SCHEDULE_TIME_QUESTION,
    SCHEDULE_TIME_SAVED,
    SCHEDULE_TIMEZONE_BUTTON,
    SCHEDULE_TIMEZONE_LINE,
    SCHEDULE_TIMEZONE_QUESTION,
    SCHEDULE_TIMEZONE_SAVED,
    SCHEDULE_TITLE,
    SETTINGS_CHOOSE,
    SETTINGS_CURRENT,
    SETTINGS_SAVED,
    SETTINGS_TITLE,
    SETTINGS_TOO_LONG,
    TIMEZONES,
    WEEKDAYS,
    parse_weekday,
    schedule_text,
    timezone_name,
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
    save: Callable[[SettingsService, WorkspaceId, str], Awaitable[str]]
    """Saves the Owner's text in the Пространство, returning the confirmation («Ниша изменена:
    …»); raises `InvalidSettingValue` (from `SettingsService`) without saving anything."""
    question: str
    """What to ask, with an example."""
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


async def _save_niche(settings: SettingsService, workspace_id: WorkspaceId, text: str) -> str:
    return f"Ниша изменена: {await settings.set_niche(workspace_id, text)}"


async def _save_persona(settings: SettingsService, workspace_id: WorkspaceId, text: str) -> str:
    persona, custom_persona = resolve_persona(await settings.set_persona(workspace_id, text))
    return f"Стиль автора изменён: {persona_display_title(persona, custom_persona)}"


async def _save_audience(settings: SettingsService, workspace_id: WorkspaceId, text: str) -> str:
    audience = await settings.set_audience(workspace_id, text)
    if audience is None:
        return "Аудитория убрана: Темы и Статьи будут без портрета читателя."
    return f"Аудитория изменена: {audience_screen_text(audience)}"


async def _save_project(settings: SettingsService, workspace_id: WorkspaceId, text: str) -> str:
    project = await settings.set_project(workspace_id, text)
    if project is None:
        return "Ссылка убрана: Статьи будут без ссылки в конце."
    return f"Канал или сайт изменён: {project_detail_text(project)}"


SETTING_FIELDS: tuple[SettingField, ...] = (
    SettingField(
        key="niche",
        label="Ниша",
        change_button="✏️ Изменить Нишу",
        purpose="из неё подбираются Темы",
        show=lambda current: current.niche if current.niche_saved else NICHE_UNSET,
        save=_save_niche,
        question=(
            "✏️ Напишите новую Нишу — тематику, в которой я подбираю Темы и пишу Статьи.\n\n"
            "Например: фитнес и здоровое питание"
        ),
        invalid="Ниша не может быть пустой.",
        invalid_by_field={"niche_too_long": SETTINGS_TOO_LONG.format(limit=NICHE_MAX_LENGTH)},
    ),
    SettingField(
        key="persona",
        label="Стиль автора",
        change_button="✏️ Изменить стиль автора",
        purpose="от чьего лица и каким тоном пишутся Статьи",
        show=lambda current: persona_display_title(current.persona, current.custom_persona),
        detail=lambda current: persona_detail_text(current.persona, current.custom_persona),
        save=_save_persona,
        question=(
            "✏️ Опишите свой стиль: каждое поле с новой строки, обязательна только «Роль».\n\n"
            "Например:\n"
            "Название: Технооптимист\n"
            "Роль: фаундер, который сам внедряет AI в процессы\n"
            "Тон: энергичный, без канцелярита\n"
            "Запрещено: выдуманные кейсы"
        ),
        invalid="Не нашёл строку «Роль: …» — без неё стиль не сохранить.",
        invalid_by_field={"persona_too_long": SETTINGS_TOO_LONG.format(limit=PERSONA_MAX_LENGTH)},
        presets=tuple(
            (persona.title, persona_setting_value(persona.key)) for persona in PERSONAS.values()
        ),
        custom_button="✏️ Свой стиль",
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
        invalid="Аудитория не может быть пустой — опишите читателя хотя бы одной фразой.",
        invalid_by_field={
            "audience_too_long": (
                f"Слишком длинно — уложитесь в {AUDIENCE_MAX_LENGTH} символов: "
                "главное о читателе, без лишних подробностей."
            )
        },
    ),
    SettingField(
        key="project",
        label="Ваш канал или сайт",
        change_button="✏️ Изменить канал или сайт",
        purpose="ссылка на него в последнем абзаце каждой Статьи",
        show=lambda current: project_detail_text(current.project),
        save=_save_project,
        question=(
            "✏️ Куда приглашать читателей в конце Статьи? Пришлите ссылку на ваш Telegram-канал "
            "или сайт — я аккуратно упомяну её в последнем абзаце, по теме Статьи.\n\n"
            f"{PROJECT_EXAMPLES}\n\n"
            "Можно через пробел добавить пару слов, что там. Чтобы убрать ссылку, отправьте «-»."
        ),
        invalid="Пришлите ссылку на канал или сайт.",
        invalid_by_field={
            "project_url": f"Не получилось распознать ссылку. {PROJECT_EXAMPLES}",
            "project_too_long": SETTINGS_TOO_LONG.format(limit=PROJECT_MAX_LENGTH),
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
    async def get(self, workspace_id: WorkspaceId) -> ScheduleConfig | None: ...

    async def set(
        self, workspace_id: WorkspaceId, day_of_week: str, hour: int, minute: int
    ) -> None: ...


class WorkspaceStore(Protocol):
    async def set_timezone(self, workspace_id: WorkspaceId, timezone: str) -> None: ...


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


def render_schedule_text(
    schedule: ScheduleConfig, timezone: str, *, notice: str | None = None
) -> str:
    now = schedule_text(schedule.day_of_week, schedule.hour, schedule.minute)
    zone = SCHEDULE_TIMEZONE_LINE.format(timezone=timezone_name(timezone))
    return _with_notice(
        f"{SCHEDULE_TITLE}\n\n{current_line(now)}\n{zone}\n\n{SCHEDULE_HOW}", notice
    )


def render_timezone_text(timezone: str) -> str:
    return f"{SCHEDULE_TIMEZONE_QUESTION}\n\n{current_line(timezone_name(timezone))}"


def build_timezone_keyboard(timezone: str, back: str) -> InlineKeyboardMarkup:
    zones = [
        _button(f"✅ {label}" if code == timezone else label, "schedule_tz_pick", f"{index}:{back}")
        for index, (code, label) in enumerate(TIMEZONES.items())
    ]
    rows = [zones[start : start + 3] for start in range(0, len(zones), 3)]
    rows.append([_button(BACK_BUTTON, "schedule", back)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


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
            [_button(SCHEDULE_TIMEZONE_BUTTON, "schedule_tz", back)],
            [back_button],
        ]
    )


class SettingsScreen:
    def __init__(
        self,
        settings: SettingsService,
        schedule: ScheduleStore,
        workspaces: WorkspaceStore,
        bot: BotClient,
        prompts: InputPrompt,
    ) -> None:
        self._settings = settings
        self._schedule = schedule
        self._workspaces = workspaces
        self._bot = bot
        self._prompts = prompts

    # --- Настройки ---

    async def send(self, workspace: Workspace, chat_id: int, *, notice: str | None = None) -> int:
        text = await self._settings_text(workspace, notice)
        return await self._bot.send_message(chat_id, text, reply_markup=build_settings_keyboard())

    async def show(
        self, workspace: Workspace, chat_id: int, message_id: int, *, notice: str | None = None
    ) -> None:
        await self._redraw(
            chat_id,
            message_id,
            await self._settings_text(workspace, notice),
            build_settings_keyboard(),
        )

    async def edit(
        self, workspace: Workspace, chat_id: int, user_id: int, message_id: int, key: str
    ) -> None:
        """«Изменить …»: the chooser for a Настройка with ready-made variants, else the question."""
        setting = _FIELDS.get(key)
        if setting is None:  # a button from a build that had a Настройка this one hasn't
            await self.show(workspace, chat_id, message_id)
            return
        if not setting.presets:
            await self.ask(workspace, chat_id, user_id, key, screen_message_id=message_id)
            return
        current = await self._settings.read(workspace.id)
        await self._redraw(
            chat_id,
            message_id,
            render_chooser_text(setting, current),
            build_chooser_keyboard(setting, current),
        )

    async def ask(
        self,
        workspace: Workspace,
        chat_id: int,
        user_id: int,
        key: str,
        *,
        screen_message_id: int | None,
    ) -> None:
        setting = _FIELDS.get(key)
        if setting is None:
            return
        await self._prompts.ask(
            workspace.id,
            chat_id,
            user_id,
            SETTING_INPUT_KIND,
            f"{key}:{screen_message_id or 0}",
            await self._question(workspace, setting),
        )

    async def pick(
        self, workspace: Workspace, chat_id: int, message_id: int, key: str, index: int
    ) -> None:
        """A ready-made variant from the chooser: saved as if typed, then back to the screen."""
        setting = _FIELDS.get(key)
        if setting is None or not 0 <= index < len(setting.presets):
            await self.show(workspace, chat_id, message_id)
            return
        _title, value = setting.presets[index]
        notice = await setting.save(self._settings, workspace.id, value)
        await self.show(workspace, chat_id, message_id, notice=notice)

    async def apply_command(
        self, workspace: Workspace, chat_id: int, user_id: int, key: str, args: str
    ) -> None:
        """The hidden `/set_<key> <value>` aliases: save and show the screen; with no value,
        ask for it the same way «Изменить» does."""
        setting = _FIELDS[key]
        if not args.strip():
            await self.ask(workspace, chat_id, user_id, key, screen_message_id=None)
            return
        try:
            notice = await setting.save(self._settings, workspace.id, args)
        except InvalidSettingValue as exc:
            problem = setting.invalid_by_field.get(exc.field, setting.invalid)
            await self._bot.send_message(chat_id, f"⚠️ {problem}\n\n{setting.question}")
            return
        await self.send(workspace, chat_id, notice=notice)

    # --- Расписание ---

    async def send_schedule(
        self, workspace: Workspace, chat_id: int, *, back: str = BACK_TO_MENU
    ) -> None:
        text, keyboard = await self._schedule_screen(workspace, back)
        await self._bot.send_message(chat_id, text, reply_markup=keyboard)

    async def show_schedule(
        self,
        workspace: Workspace,
        chat_id: int,
        message_id: int,
        back: str,
        *,
        notice: str | None = None,
    ) -> None:
        await self._redraw(
            chat_id, message_id, *await self._schedule_screen(workspace, back, notice)
        )

    async def set_day(
        self, workspace: Workspace, chat_id: int, message_id: int, day: str, back: str
    ) -> None:
        if day not in WEEKDAYS:
            await self.show_schedule(workspace, chat_id, message_id, back)
            return
        current = await self._schedule_config(workspace.id)
        saved = await self._save_schedule(workspace.id, day, current.hour, current.minute)
        await self.show_schedule(
            workspace, chat_id, message_id, back, notice=SCHEDULE_DAY_SAVED.format(schedule=saved)
        )

    async def show_timezones(
        self, workspace: Workspace, chat_id: int, message_id: int, back: str
    ) -> None:
        await self._redraw(
            chat_id,
            message_id,
            render_timezone_text(workspace.timezone),
            build_timezone_keyboard(workspace.timezone, back),
        )

    async def set_timezone(
        self, workspace: Workspace, chat_id: int, message_id: int, index: str, back: str
    ) -> None:
        """`index` into `TIMEZONES`, from the chooser's button; anything else just shows the
        Расписание again."""
        zones = list(TIMEZONES)
        if not index.isdigit() or int(index) >= len(zones):
            await self.show_schedule(workspace, chat_id, message_id, back)
            return
        timezone = zones[int(index)]
        await self._workspaces.set_timezone(workspace.id, timezone)
        await self.show_schedule(
            replace(workspace, timezone=timezone),
            chat_id,
            message_id,
            back,
            notice=SCHEDULE_TIMEZONE_SAVED.format(timezone=timezone_name(timezone)),
        )

    async def ask_time(
        self, workspace: Workspace, chat_id: int, user_id: int, message_id: int, back: str
    ) -> None:
        await self._prompts.ask(
            workspace.id,
            chat_id,
            user_id,
            SETTING_INPUT_KIND,
            f"{_TIME_KEY}:{message_id}:{back}",
            await self._time_question(workspace.id),
        )

    async def set_schedule_command(self, workspace: Workspace, chat_id: int, args: str) -> None:
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
        saved = await self._save_schedule(workspace.id, day, *parsed)
        await self._bot.send_message(chat_id, f"Расписание изменено: {saved}")

    # --- typed answers ---

    async def handle_reply(
        self, workspace: Workspace, chat_id: int, user_id: int, text: str
    ) -> bool:
        """A message that may answer one of this screen's questions; `False` if it doesn't."""
        pending = await self._prompts.take(workspace.id, chat_id, user_id, SETTING_INPUT_KIND)
        if pending is None:
            return False
        key, _, rest = pending.target_id.partition(":")
        message_id_text, _, back = rest.partition(":")
        screen_message_id = int(message_id_text or 0) or None

        if key == _TIME_KEY:
            parsed = parse_time(text)
            if parsed is None:
                problem = SCHEDULE_TIME_INVALID.format(text=text.strip()[:40])
                await self._prompts.ask_again(
                    workspace.id,
                    chat_id,
                    user_id,
                    pending,
                    problem,
                    await self._time_question(workspace.id),
                )
                return True
            current = await self._schedule_config(workspace.id)
            saved = await self._save_schedule(workspace.id, current.day_of_week, *parsed)
            await self._prompts.close(chat_id, pending)
            notice = SCHEDULE_TIME_SAVED.format(schedule=saved)
            await self._redraw(
                chat_id,
                screen_message_id,
                *await self._schedule_screen(workspace, back or BACK_TO_MENU, notice),
            )
            return True

        setting = _FIELDS.get(key)
        if setting is None:
            await self._prompts.close(chat_id, pending)
            return True
        try:
            notice = await setting.save(self._settings, workspace.id, text)
        except InvalidSettingValue as exc:
            problem = setting.invalid_by_field.get(exc.field, setting.invalid)
            await self._prompts.ask_again(
                workspace.id,
                chat_id,
                user_id,
                pending,
                problem,
                await self._question(workspace, setting),
            )
            return True
        await self._prompts.close(chat_id, pending)
        await self._redraw(
            chat_id,
            screen_message_id,
            await self._settings_text(workspace, notice),
            build_settings_keyboard(),
        )
        return True

    # --- helpers ---

    async def _settings_text(self, workspace: Workspace, notice: str | None) -> str:
        return render_settings_text(
            await self._settings.read(workspace.id),
            await self._schedule_config(workspace.id),
            notice=notice,
        )

    async def _question(self, workspace: Workspace, setting: SettingField) -> str:
        current = await self._settings.read(workspace.id)
        value = (setting.detail or setting.show)(current)
        return f"{setting.question}\n\n{current_line(value)}"

    async def _time_question(self, workspace_id: WorkspaceId) -> str:
        schedule = await self._schedule_config(workspace_id)
        now = schedule_text(schedule.day_of_week, schedule.hour, schedule.minute)
        return f"{SCHEDULE_TIME_QUESTION}\n\n{current_line(now)}"

    async def _schedule_config(self, workspace_id: WorkspaceId) -> ScheduleConfig:
        return await self._schedule.get(workspace_id) or DEFAULT_SCHEDULE

    async def _schedule_screen(
        self, workspace: Workspace, back: str, notice: str | None = None
    ) -> tuple[str, InlineKeyboardMarkup]:
        schedule = await self._schedule_config(workspace.id)
        return (
            render_schedule_text(schedule, workspace.timezone, notice=notice),
            build_schedule_keyboard(schedule, back),
        )

    async def _save_schedule(
        self, workspace_id: WorkspaceId, day: str, hour: int, minute: int
    ) -> str:
        await self._schedule.set(workspace_id, day, hour, minute)
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
