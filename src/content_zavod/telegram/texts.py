"""texts: the Russian a content manager reads instead of the domain's English keys (#89).

Statuses, Площадка keys, `job_type` and day-of-week codes stay English in the DB and the
domain layer; the Telegram layer renders them through the lookups below. Every key lookup
falls back to the raw key, so a value added to the domain before it's added here still
renders - untranslated, but never a crash. Job failures and callback alerts deliberately
have no raw-key fallback: their technical text goes to the log, never to the chat.
"""

from __future__ import annotations

from datetime import date

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

# The Хаб (#91): the approved Plan message as a checklist of each Тема's cover and Статьи,
# then a table of contents into each Тема's result card.
HUB_MARKS = {"pending": "⏳", "ready": "✅", "failed": "❌"}
HUB_ARTICLE_STATES = {"pending": "пишется", "ready": "готова", "failed": "не получилась"}
# A Статья whose redo (✏️ Доработать / 🔁 Повторить) failed still has its previous Версия.
HUB_ARTICLE_FAILED_KEPT = "не получилась, открыта прежняя версия"
HUB_COVER_STATES = {"pending": "рисуется", "ready": "готова", "failed": "не получилась"}
HUB_COVER_SHORT = "🖼"
HUB_PROGRESS = "⏳ Готовлю обложки и Статьи: готово {done} из {total}"
HUB_PROGRESS_HINT = "Готовые Темы уже можно открыть кнопками ниже."
HUB_DONE = "✅ Всё готово. Откройте Тему кнопкой ниже."
HUB_DONE_WITH_FAILURES = "⚠️ Готово, но не всё получилось ({failed} из {total}). Можно повторить."
HUB_EMPTY = "В Плане не осталось Тем."
HUB_TOPIC_HEADER = "📂 Тема {number} из {total}"
HUB_TOPIC_COVER_LINE = "🖼 Обложка — {mark} {state}"
HUB_TOPIC_ARTICLE_LINE = "📄 {platform} — {mark} {state}"
HUB_TOPIC_HINT = (
    "📖 — прочитать Статью прямо в Telegram.\n"
    "📄 — карточка Статьи отдельным сообщением: скачать .docx/.md, доработать, "
    "отметить готовой."
)
HUB_BUTTON_COVER = "🖼 Обложка"
HUB_BUTTON_ARTICLE = "📄 {platform}"
HUB_BUTTON_READ = "📖 {platform}"
HUB_BUTTON_RETRY_TOPIC = "🔁 Повторить"
HUB_BUTTON_RETRY_ALL = "🔁 Повторить неудавшееся"
HUB_BUTTON_BACK = "◀ К Плану"
HUB_ALERT_NO_COVER = "Обложки пока нет."
HUB_ALERT_RETRYING = "Повторяю..."
HUB_ALERT_NOTHING_TO_RETRY = "Повторять нечего."
COVER_CAPTION = "🖼 Обложка: {title}"
COVER_REQUESTED = "Генерирую обложку — пришлю её сюда."

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


_MONTHS_RU_GENITIVE = (
    "января",
    "февраля",
    "марта",
    "апреля",
    "мая",
    "июня",
    "июля",
    "августа",
    "сентября",
    "октября",
    "ноября",
    "декабря",
)


def format_week_range(week_label: str) -> str:
    """Render an ISO week_label (e.g. "2026-W33") as a human date range, e.g.
    "10–16 августа 2026". `week_label` itself stays the Plan's idempotency
    key (see `week_label_for` in scheduling/weekly_plan_trigger.py) and is
    never shown to the Контент-менеджер directly."""
    year_part, _, week_part = week_label.partition("-W")
    monday = date.fromisocalendar(int(year_part), int(week_part), 1)
    sunday = date.fromisocalendar(int(year_part), int(week_part), 7)
    start_month = _MONTHS_RU_GENITIVE[monday.month - 1]
    end_month = _MONTHS_RU_GENITIVE[sunday.month - 1]
    if monday.year != sunday.year:
        return f"{monday.day} {start_month} {monday.year} – {sunday.day} {end_month} {sunday.year}"
    if monday.month != sunday.month:
        return f"{monday.day} {start_month} – {sunday.day} {end_month} {sunday.year}"
    return f"{monday.day}–{sunday.day} {end_month} {sunday.year}"


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


def hub_mark(state: str) -> str:
    return HUB_MARKS.get(state, state)


def hub_article_state(state: str, *, has_content: bool = False) -> str:
    if state == "failed" and has_content:
        return HUB_ARTICLE_FAILED_KEPT
    return HUB_ARTICLE_STATES.get(state, state)


def hub_cover_state(state: str) -> str:
    return HUB_COVER_STATES.get(state, state)


def error_alert_text(exc: Exception) -> str:
    return ERROR_ALERTS.get(type(exc), ERROR_ALERT_FALLBACK)
