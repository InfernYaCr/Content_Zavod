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
