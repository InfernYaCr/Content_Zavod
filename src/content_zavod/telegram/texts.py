"""texts: the Russian a content manager reads instead of the domain's English keys (#89).

Statuses, Площадка keys, `job_type` and day-of-week codes stay English in the DB and the
domain layer; the Telegram layer renders them through the lookups below. Every key lookup
falls back to the raw key, so a value added to the domain before it's added here still
renders - untranslated, but never a crash. Job failures and callback alerts deliberately
have no raw-key fallback: their technical text goes to the log, never to the chat.
"""

from __future__ import annotations

from ..access.errors import (
    CannotRemoveSelf,
    JoinRequestNotFound,
    LastOwnerRemoval,
    MemberNotFound,
)
from ..domain.errors import (
    ArticleNotFound,
    ArticleNotReady,
    ArticleNotRegenerable,
    ArticleVersionNotFound,
    InvalidSettingValue,
    PlanItemNotEditable,
    PlanItemNotFound,
    PlanNotFound,
)

ARTICLE_STATUSES = {
    "queued": "в очереди",
    "generating": "пишется",
    "error": "ошибка",
    "ready": "готова",
    "regenerating": "переписывается",
    "exported": "принята",
}

PLAN_STATUSES = {
    "pending_review": "на согласовании",
    "approved": "утверждён",
    "archived": "в архиве",
}

# Тема (PlanItem) statuses, feminine to agree with «Тема».
TOPIC_STATUSES = {
    "pending_review": "на согласовании",
    "approved": "утверждена",
    "rejected": "убрана",
    "archived": "в архиве",
}

ROLES = {"owner": "Владелец", "content_manager": "Контент-менеджер"}

PLATFORMS = {"zen": "Дзен", "vc": "VC.ru"}
# «Статья для …»: Дзен declines, VC.ru doesn't.
_PLATFORMS_GENITIVE = {"zen": "Дзена", "vc": "VC.ru"}

# code -> (short, full); `mon`..`sun` are what APScheduler's CronTrigger and the DB expect.
WEEKDAYS = {
    "mon": ("пн", "понедельник"),
    "tue": ("вт", "вторник"),
    "wed": ("ср", "среда"),
    "thu": ("чт", "четверг"),
    "fri": ("пт", "пятница"),
    "sat": ("сб", "суббота"),
    "sun": ("вс", "воскресенье"),
}

# The ready-Статья card (#92).
ARTICLE_CARD_PLATFORM = "Площадка: {platform}"
# #94: the Версия was written without verified evidence - the warning lives on the card,
# never in the Статья text (that one is exported and published as is).
ARTICLE_CARD_NO_EVIDENCE = {
    "no_evidence": "⚠️ Источники не найдены — проверьте факты перед публикацией",
    "search_unavailable": (
        "⚠️ Поиск источников был недоступен — проверьте факты перед публикацией "
        "или запросите Перегенерацию позже"
    ),
}
READ_BUTTON = "📖 Читать"
REFINE_BUTTON = "✏️ Доработать"

JOB_FAILURES = {
    "generate_plan": "Не удалось составить План.",
    "regenerate_topic": "Не удалось перегенерировать Тему.",
    "generate_article": "Не удалось написать Статью.",
    "regenerate_article": "Не удалось переписать Статью.",
    "generate_cover": "Не удалось сгенерировать обложку.",
}
JOB_FAILURE_FALLBACK = "Не удалось выполнить задачу."

# The same failures once the Тема (and, for a Статья, its Площадка) is known - a chat
# with several Статьи in flight needs to say which one failed (#89).
_ARTICLE_JOB_FAILURES = {
    "generate_article": "Не удалось написать Статью для {platform}: «{title}»",
    "regenerate_article": "Не удалось переписать Статью для {platform}: «{title}»",
}
_COVER_JOB_FAILURE = "Не удалось сгенерировать обложку для Темы «{title}»"

ERROR_ALERTS: dict[type[Exception], str] = {
    PlanNotFound: "План не найден.",
    PlanItemNotFound: "Тема не найдена.",
    PlanItemNotEditable: "Эту Тему уже нельзя изменить.",
    ArticleNotFound: "Статья не найдена.",
    ArticleNotReady: "Статья ещё не готова.",
    ArticleNotRegenerable: "Эту Статью сейчас нельзя перегенерировать.",
    ArticleVersionNotFound: "Версия Статьи не найдена.",
    InvalidSettingValue: "Значение не может быть пустым.",
    MemberNotFound: "Участник не найден.",
    JoinRequestNotFound: "Заявка не найдена.",
    CannotRemoveSelf: "Нельзя удалить самого себя.",
    LastOwnerRemoval: "Нельзя удалить последнего Владельца.",
}
ERROR_ALERT_FALLBACK = "Не получилось, попробуйте ещё раз"


def article_status(status: str) -> str:
    return ARTICLE_STATUSES.get(status, status)


def plan_status(status: str) -> str:
    return PLAN_STATUSES.get(status, status)


def topic_status(status: str) -> str:
    return TOPIC_STATUSES.get(status, status)


def role_name(role: str) -> str:
    return ROLES.get(role, role)


def platform_name(platform: str) -> str:
    return PLATFORMS.get(platform, platform)


def weekday_name(code: str) -> str:
    """Full Russian name for a stored day code, e.g. `mon` -> «понедельник»."""
    return WEEKDAYS[code][1] if code in WEEKDAYS else code


def parse_weekday(text: str) -> str | None:
    """`/set_schedule` input -> stored day code: the English code itself, the Russian short
    form or the full name, any case. `None` for anything else."""
    text = text.lower()
    for code, names in WEEKDAYS.items():
        if text == code or text in names:
            return code
    return None


def job_failure_text(
    job_type: str, *, title: str | None = None, platform: str | None = None
) -> str:
    """«Не удалось …» for a failed Job, naming the Тема and Площадка when the caller knows
    them; the bare per-type text otherwise."""
    if title is not None:
        if platform is not None and job_type in _ARTICLE_JOB_FAILURES:
            return _ARTICLE_JOB_FAILURES[job_type].format(
                platform=_PLATFORMS_GENITIVE.get(platform, platform_name(platform)), title=title
            )
        if job_type == "generate_cover":
            return _COVER_JOB_FAILURE.format(title=title)
    return JOB_FAILURES.get(job_type, JOB_FAILURE_FALLBACK)


def error_alert_text(exc: Exception) -> str:
    return ERROR_ALERTS.get(type(exc), ERROR_ALERT_FALLBACK)


# --- Главное меню, Экран Настроек, Расписание (#95) ---
# Per-Настройка texts (question, example, confirmation) live next to their entry in
# `settings_screen.SETTING_FIELDS`, so adding a Настройка stays one entry; these are the
# shared ones.

BACK_BUTTON = "◀ Назад"
CANCEL_BUTTON = "Отмена"

WELCOME_TEXT = (
    "👋 Добро пожаловать! Я составляю для команды План Тем на неделю "
    "и пишу по нему Статьи для Дзена и VC.ru."
)
MENU_TEXT = "🏠 Главное меню\nВыберите, что сделать:"
MENU_PLAN_BUTTON = "📋 План недели"
MENU_TOPIC_BUTTON = "✍️ Предложить Тему"
MENU_HISTORY_BUTTON = "🗂 История"
MENU_SETTINGS_BUTTON = "⚙️ Настройки"
MENU_MEMBERS_BUTTON = "👥 Участники"
MENU_SCHEDULE_BUTTON = "🕘 Расписание"
OPEN_MENU_BUTTON = "🏠 Открыть меню"
# Under the История and Участники screens, which the menu edits in place (their own
# «◀ Назад» already means "previous page").
TO_MENU_BUTTON = "🏠 В меню"

HELP_TEXT = (
    "Я составляю для команды План Тем на неделю и пишу по нему Статьи для Дзена и VC.ru.\n\n"
    "Всё делается кнопками в меню — откройте его командой /menu:\n"
    "📋 План недели — где сейчас План и как составить новый\n"
    "✍️ Предложить Тему — добавить свою Тему в План\n"
    "🗂 История — прошлые Планы и готовые Статьи"
)
HELP_OWNER_TEXT = (
    "\n\nТолько для Владельца:\n"
    "⚙️ Настройки — Ниша, Персона, Направления, Проект\n"
    "👥 Участники — у кого есть доступ к боту\n"
    "🕘 Расписание — когда составляется новый План"
)
UNKNOWN_MESSAGE_TEXT = "Чтобы что-то сделать, откройте меню 👇"

PLAN_OPEN_BUTTON = "📋 Открыть План"
PLAN_GENERATE_BUTTON = "🪄 Составить План"
PLAN_STATE_PENDING = "ждёт согласования"
PLAN_STATE_APPROVED = "утверждён"
PLAN_IN_TEAM_CHAT = "Он в чате команды — откройте его кнопкой ниже."
PLAN_IN_TEAM_CHAT_NO_LINK = "Он в чате команды — найдите там сообщение с Планом."
PLAN_MISSING_TEXT = (
    "📋 Плана на {week} пока нет.\n"
    "Новый План составляется автоматически по расписанию: {schedule}.\n"
    "Можно составить его прямо сейчас."
)

TOPIC_QUESTION = (
    "✍️ Напишите Тему — она добавится в План этой недели.\n\n"
    "Например: Как малому бизнесу посчитать окупаемость рекламы"
)
TOPIC_PLACEHOLDER = "Тема для Плана"

# Appended to every typed-input question (#88's two chat shapes): a private chat takes the
# next message as is, a group needs a reply to the ForceReply line that follows.
INPUT_HINT_PRIVATE = "Напишите ответ следующим сообщением или нажмите «Отмена»."
INPUT_HINT_GROUP = "Ответьте на сообщение ниже или нажмите «Отмена»."
INPUT_FORCE_REPLY = '✏️ <a href="tg://user?id={user_id}">Ваш ответ</a> — ответом на это сообщение.'

SETTINGS_TITLE = "⚙️ Настройки\nДействуют на каждую следующую генерацию."
SETTINGS_CURRENT = "Сейчас: {value}"
SETTINGS_CHOOSE = "Выберите готовый вариант или задайте свой."
SETTINGS_SAVED = "✅ {text}"
SETTINGS_INVALID = "⚠️ {text}"

SCHEDULE_LABEL = "Расписание"
SCHEDULE_PURPOSE = "когда автоматически составляется новый План"
SCHEDULE_CHANGE_BUTTON = "🕘 Изменить Расписание"
SCHEDULE_TITLE = "🕘 Расписание\nНовый План составляется автоматически раз в неделю."
SCHEDULE_HOW = "Нажмите день недели, чтобы сменить его, или «Изменить время»."
SCHEDULE_TIME_BUTTON = "🕐 Изменить время"
SCHEDULE_DAY_SAVED = "День изменён: {schedule}"
SCHEDULE_TIME_SAVED = "Время изменено: {schedule}"
SCHEDULE_TIME_QUESTION = (
    "🕐 Во сколько составлять новый План? Напишите время в формате ЧЧ:ММ.\n\nНапример: 09:30"
)
SCHEDULE_TIME_PLACEHOLDER = "Например: 09:30"
SCHEDULE_TIME_INVALID = "Не понял время «{text}». Нужно ЧЧ:ММ, например 09:30."


def schedule_text(day: str, hour: int, minute: int) -> str:
    """«понедельник, 09:00» - the one way a schedule is shown on every screen."""
    return f"{weekday_name(day)}, {hour:02d}:{minute:02d}"
