"""texts: the Russian a content manager reads instead of the domain's English keys (#89).

Statuses, Площадка keys, `job_type` and day-of-week codes stay English in the DB and the
domain layer; the Telegram layer renders them through the lookups below. Every key lookup
falls back to the raw key, so a value added to the domain before it's added here still
renders - untranslated, but never a crash. Job failures and callback alerts deliberately
have no raw-key fallback: their technical text goes to the log, never to the chat.
"""

from __future__ import annotations

from datetime import date, datetime

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
from ..job_queue import JobNotFound
from ..workspaces import (
    DailyFreeJobLimitReached,
    DailyJobLimitReached,
    DailyPlanLimitReached,
    GenerationBusy,
    TrialLimitExhausted,
    Usage,
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

PLATFORMS = {"zen": "Дзен", "vc": "VC.ru"}
# «Статья для …»: Дзен declines, VC.ru doesn't.
_PLATFORMS_GENITIVE = {"zen": "Дзена", "vc": "VC.ru"}

# code -> (short, full); `mon`..`sun` are what the DB and the Расписание tick expect.
WEEKDAYS = {
    "mon": ("пн", "понедельник"),
    "tue": ("вт", "вторник"),
    "wed": ("ср", "среда"),
    "thu": ("чт", "четверг"),
    "fri": ("пт", "пятница"),
    "sat": ("сб", "суббота"),
    "sun": ("вс", "воскресенье"),
}

# The Расписание's time zones (ADR-0015): Russia's, by offset from Moscow, no free IANA input.
# IANA name -> label; the screen's buttons carry the index in this order.
TIMEZONES = {
    "Europe/Kaliningrad": "МСК−1",
    "Europe/Moscow": "МСК",
    "Europe/Samara": "МСК+1",
    "Asia/Yekaterinburg": "МСК+2",
    "Asia/Omsk": "МСК+3",
    "Asia/Krasnoyarsk": "МСК+4",
    "Asia/Irkutsk": "МСК+5",
    "Asia/Yakutsk": "МСК+6",
    "Asia/Vladivostok": "МСК+7",
    "Asia/Magadan": "МСК+8",
    "Asia/Kamchatka": "МСК+9",
}

# #94: the Версия was written without verified evidence - the warning lives on the card,
# never in the Статья text (that one is exported and published as is).
ARTICLE_CARD_NO_EVIDENCE = {
    "no_evidence": "⚠️ Источники не найдены — проверьте факты перед публикацией",
    "search_unavailable": (
        "⚠️ Поиск источников был недоступен — проверьте факты перед публикацией "
        "или запросите Перегенерацию позже"
    ),
}
# The buttons of a Статья card sent before #143 - its ✅ still redraws them.
READ_BUTTON = "📖 Читать"
REFINE_BUTTON = "✏️ Доработать"

# «✏️ Попросить доработать» (#143) and 🔄 on a Тема: the question sent below, with its buttons,
# and what it turns into once the regeneration is queued (#80).
TOPIC_COMMENT_QUESTION = (
    "Комментарий к перегенерации? Напишите его следующим сообщением или нажмите «Пропустить»."
)
COMMENT_SKIP_BUTTON = "Пропустить"
COMMENT_CANCEL_BUTTON = "Отмена"
COMMENT_GENERATING = "⏳ Генерирую..."
ARTICLE_COMMENT_QUESTION = (
    "✏️ Что поправить в Статье для {platform}? Напишите следующим сообщением — например, "
    "«короче» или «добавь пример из практики». Или нажмите «Переписать без пожеланий»."
)
ARTICLE_COMMENT_SKIP_BUTTON = "🔄 Переписать без пожеланий"
ARTICLE_REWRITING = "⏳ Переписываю… пришлю, как будет готова"

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

# The Хаб (#91, #143): the approved Plan message - the checklist of Темы, a Тема's screen and
# a Статья's screen, all in that one message.
HUB_MARKS = {"pending": "⏳", "ready": "✅", "failed": "❌"}
# A Статья marked «Готово, публикую» (#143).
HUB_MARK_EXPORTED = "👍"
HUB_ARTICLE_STATES = {
    "pending": "пишется",
    "ready": "готова, ждёт проверки",
    "failed": "не получилась",
}
HUB_ARTICLE_EXPORTED = "отмечена готовой"
# ✏️ / 🔁 on a Статья that already has a Версия.
HUB_ARTICLE_REWRITING = "переписывается"
# A Статья whose redo (✏️ / 🔁 Повторить) failed still has its previous Версия.
HUB_ARTICLE_FAILED_KEPT = "не получилась, открыта прежняя версия"
HUB_COVER_STATES = {"pending": "рисуется", "ready": "готова", "failed": "не получилась"}
HUB_COVER_SHORT = "🖼"
HUB_PROGRESS = "⏳ В работе: готово {done} из {total}"
HUB_DONE = "✅ Всё готово."
HUB_DONE_WITH_FAILURES = "⚠️ Готово, но не всё получилось ({failed} из {total}). Можно повторить."
HUB_EMPTY = "В Плане не осталось Тем."
HUB_NEXT = "Что дальше: откройте Тему кнопкой ниже — внутри её Статьи."
# The Тема screen.
HUB_TOPIC_HEADER = "📂 Тема {number} из {total}"
HUB_TOPIC_ARTICLE_LINE = "• {platform} — {mark} {state}"
HUB_TOPIC_COVER_LINE = "• 🖼 Обложка — {mark} {state}"
HUB_TOPIC_NO_EVIDENCE = {
    "no_evidence": "⚠️ Источники не найдены — проверьте факты ({platform})",
    "search_unavailable": "⚠️ Поиск источников был недоступен — проверьте факты ({platform})",
}
HUB_TOPIC_NEXT = (
    "Что дальше: откройте Статью и прочитайте → если нравится, нажмите «Готово, публикую», "
    "нужно поправить — «Попросить доработать»."
)
HUB_TOPIC_NEXT_WRITING = "Что дальше: Статьи ещё пишутся — это сообщение обновится само."
HUB_TOPIC_NEXT_DONE = (
    "Что дальше: все Статьи Темы готовы к публикации. Вернитесь к Плану за следующей Темой."
)
# A rewrite's result moves the Хаб down the chat (#143) - this message won't update itself.
HUB_TOPIC_NEXT_REWRITING = "Что дальше: Статьи переписываются — пришлю, как будут готовы."
HUB_TOPIC_NEXT_RETRY = "Что дальше: Статьи не получились — нажмите «🔁 Повторить»."
# The Статья screen: a summary instead of a cut-off text.
HUB_ARTICLE_HEADER = "📄 Статья для {platform}"
HUB_ARTICLE_STATUS = {
    "ready": "✅ Готова — прочитайте и проверьте",
    "exported": "👍 Отмечена готовой — можно публиковать",
    "pending": "⏳ Переписываю — пока открыта прежняя версия",
    "failed": "❌ Переписать не получилось — открыта прежняя версия",
}
# «мин», not «минута/минуты/минут»: no plural to get wrong.
HUB_ARTICLE_SIZE = "≈ {chars} знаков · {minutes} мин чтения"
HUB_ARTICLE_OPENING = "Начало: «{opening}»"
HUB_ARTICLE_NEXT = {
    "ready": (
        "Что дальше: «Читать полностью» → если всё хорошо, «Готово, публикую». "
        "Для публикации скачайте Word."
    ),
    "exported": "Что дальше: скачайте Word и публикуйте. Следующая Статья — «◀ К Теме».",
    "pending": "Что дальше: пришлю новую версию, как будет готова.",
    "failed": "Что дальше: попросите доработать ещё раз или скачайте прежнюю версию.",
}
# A ready Статья whose Страница для чтения couldn't be published: no «📖» to point at.
HUB_ARTICLE_NEXT_NO_PAGE = (
    "Что дальше: скачайте Word и прочитайте → если всё хорошо, «Готово, публикую»."
)
HUB_BUTTON_COVER = "🖼 Обложка"
HUB_BUTTON_REDRAW_COVER = "🎨 Перерисовать"
HUB_BUTTON_ARTICLE = "📄 Статья для {platform} ›"
HUB_BUTTON_RETRY_TOPIC = "🔁 Повторить"
HUB_BUTTON_RETRY_ALL = "🔁 Повторить неудавшееся"
HUB_BUTTON_BACK = "◀ К Плану"
HUB_BUTTON_TO_TOPIC = "◀ К Теме"
HUB_BUTTON_READ_FULL = "📖 Читать полностью ↗"
HUB_BUTTON_APPROVE = "✅ Готово, публикую"
HUB_BUTTON_APPROVED = "👍 Отмечено готовой"
HUB_BUTTON_REFINE = "✏️ Попросить доработать"
HUB_BUTTON_DOCX = "⬇️ Word (.docx)"
HUB_BUTTON_MD = "⬇️ Текст (.md)"
HUB_ALERT_NO_COVER = "Обложки пока нет."
HUB_ALERT_RETRYING = "Повторяю..."
HUB_ALERT_NOTHING_TO_RETRY = "Повторять нечего."
HUB_ALERT_APPROVED = "Отмечено: готово к публикации"
FILE_COMING = "Файл пришлю ниже ⬇️"
COVER_CAPTION = "🖼 Обложка: {title}"
COVER_REQUESTED = "Генерирую обложку — пришлю её сюда."
COVERS_OFF = "Обложки сейчас выключены."

# A button naming an object this Пространство doesn't have - deleted, another Workspace's, or
# never existed - gets the same answer: it must not hint that the id exists elsewhere
# (ADR-0015, «Безопасность»).
STALE_BUTTON = "Кнопка устарела."
ERROR_ALERTS: dict[type[Exception], str] = {
    PlanNotFound: STALE_BUTTON,
    PlanItemNotFound: STALE_BUTTON,
    ArticleNotFound: STALE_BUTTON,
    ArticleVersionNotFound: STALE_BUTTON,
    JobNotFound: STALE_BUTTON,
    PlanItemNotEditable: "Эту Тему уже нельзя изменить.",
    ArticleNotReady: "Статья ещё не готова.",
    ArticleNotRegenerable: "Эту Статью сейчас нельзя перегенерировать.",
    InvalidSettingValue: "Значение не может быть пустым.",
    # ADR-0015, «Защита от лавины»: one generation at a time, and a daily ceiling of Jobs.
    GenerationBusy: "⏳ Дождитесь окончания текущей генерации — потом можно продолжить.",
    DailyJobLimitReached: "На сегодня генераций достаточно — продолжим завтра.",
    DailyPlanLimitReached: "Планов на сегодня составлено достаточно — новый можно будет завтра.",
    DailyFreeJobLimitReached: (
        "Перегенераций Тем и обложек на сегодня достаточно — продолжим завтра."
    ),
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


def format_week_range(week_label: str, *, year: bool = True) -> str:
    """Render an ISO week_label (e.g. "2026-W33") as a human date range, e.g.
    "10–16 августа 2026" ("10–16 августа" without `year`). `week_label` itself stays the
    Plan's idempotency key (see `week_label_for` in scheduling/weekly_plan_trigger.py) and is
    never shown to the Владелец directly."""
    year_part, _, week_part = week_label.partition("-W")
    monday = date.fromisocalendar(int(year_part), int(week_part), 1)
    sunday = date.fromisocalendar(int(year_part), int(week_part), 7)
    start_month = _MONTHS_RU_GENITIVE[monday.month - 1]
    end_month = _MONTHS_RU_GENITIVE[sunday.month - 1]
    if monday.year != sunday.year and year:
        return f"{monday.day} {start_month} {monday.year} – {sunday.day} {end_month} {sunday.year}"
    if monday.month != sunday.month:
        text = f"{monday.day} {start_month} – {sunday.day} {end_month}"
    else:
        text = f"{monday.day}–{sunday.day} {end_month}"
    return f"{text} {sunday.year}" if year else text


def article_status(status: str) -> str:
    return ARTICLE_STATUSES.get(status, status)


def plan_status(status: str) -> str:
    return PLAN_STATUSES.get(status, status)


def topic_status(status: str) -> str:
    return TOPIC_STATUSES.get(status, status)


def platform_name(platform: str) -> str:
    return PLATFORMS.get(platform, platform)


def platform_genitive(platform: str) -> str:
    """«для Дзена», «для VC.ru»."""
    return _PLATFORMS_GENITIVE.get(platform, platform_name(platform))


def weekday_name(code: str) -> str:
    """Full Russian name for a stored day code, e.g. `mon` -> «понедельник»."""
    return WEEKDAYS[code][1] if code in WEEKDAYS else code


def timezone_name(timezone: str) -> str:
    """«МСК+7» for a stored IANA name; one outside `TIMEZONES` (an `APP_TIMEZONE` set to
    something else) is shown as it is."""
    return TIMEZONES.get(timezone, timezone)


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
                platform=platform_genitive(platform), title=title
            )
        if job_type == "generate_cover":
            return _COVER_JOB_FAILURE.format(title=title)
    return JOB_FAILURES.get(job_type, JOB_FAILURE_FALLBACK)


def hub_mark(state: str) -> str:
    return HUB_MARKS.get(state, state)


def hub_article_state(state: str, *, has_content: bool = False, exported: bool = False) -> str:
    if state == "ready" and exported:
        return HUB_ARTICLE_EXPORTED
    if state == "failed" and has_content:
        return HUB_ARTICLE_FAILED_KEPT
    if state == "pending" and has_content:
        return HUB_ARTICLE_REWRITING
    return HUB_ARTICLE_STATES.get(state, state)


def hub_cover_state(state: str) -> str:
    return HUB_COVER_STATES.get(state, state)


def error_alert_text(exc: Exception) -> str:
    if isinstance(exc, TrialLimitExhausted):
        return limit_exhausted_text(exc)
    return ERROR_ALERTS.get(type(exc), ERROR_ALERT_FALLBACK)


# --- Лимит (ADR-0015) ---


def _plural(count: int, one: str, few: str, many: str) -> str:
    if count % 10 == 1 and count % 100 != 11:
        return one
    if 2 <= count % 10 <= 4 and not 12 <= count % 100 <= 14:
        return few
    return many


def articles_text(count: int) -> str:
    return f"{count} {_plural(count, 'Статья', 'Статьи', 'Статей')}"


def topics_text(count: int) -> str:
    return f"{count} {_plural(count, 'Тема', 'Темы', 'Тем')}"


def menu_limit_text(usage: Usage, contact: str | None) -> str:
    """The Главное меню's Лимит block (#149): «Планов: использован 1 из 1», «Статей: осталось
    2 из 8» - a number after a label, so no noun has to agree with it."""
    title = MENU_ACCESS_CUSTOM if usage.custom else MENU_ACCESS_TRIAL
    if usage.plans_left == 0 and usage.articles_left == 0:
        over = f"{title} {MENU_ACCESS_OVER}"
        return f"{over} — напишите {contact}" if contact else f"{over}."
    used = _plural(usage.plans_used, "использован", "использовано", "использовано")
    lines = [
        title,
        f"• Планов: {used} {usage.plans_used} из {usage.plan_limit}",
        f"• Статей: осталось {usage.articles_left} из {usage.article_limit}",
        MENU_LIMIT_NOTE,
    ]
    if contact:
        lines.append(f"Нужно больше — напишите {contact}")
    return "\n".join(lines)


def next_plan_text(when: datetime) -> str:
    """«🕘 Следующий План — пн, 20 октября, 09:00», `when` in the Пространство's time zone."""
    day = list(WEEKDAYS.values())[when.weekday()][0]
    month = _MONTHS_RU_GENITIVE[when.month - 1]
    return f"🕘 Следующий План — {day}, {when.day} {month}, {when:%H:%M}"


def _articles_accusative(count: int) -> str:
    """«хватит на 1 Статью, 2 Статьи, 5 Статей»."""
    return f"{count} {_plural(count, 'Статью', 'Статьи', 'Статей')}"


def _with_contact(text: str, contact: str | None) -> str:
    return f"{text}\nЧтобы продолжить, напишите {contact}." if contact else text


def limit_exhausted_text(exc: TrialLimitExhausted) -> str:
    """A refused generation (fits a callback alert, 200 characters): what is left when some
    Статьи are, but fewer than asked (a «🔁 Повторить» of several), else the used-up Лимит."""
    usage = exc.usage
    if exc.articles > usage.articles_left > 0:
        text = (
            f"Пробного лимита хватит ещё на {_articles_accusative(usage.articles_left)}, "
            f"а нужно {exc.articles}."
        )
    else:
        text = (
            f"Пробный лимит исчерпан: Планов {usage.plans_used} из {usage.plan_limit}, "
            f"Статей {usage.articles_used} из {usage.article_limit}."
        )
    return _with_contact(text, exc.contact)


def approve_shortfall_text(exc: TrialLimitExhausted, *, per_tema: int) -> str:
    """«Утвердить всё» asking for more Статьи than are left: removing Темы helps only while the
    remainder still fits one Тема (`per_tema` Статьи, one per Площадка)."""
    left = exc.usage.articles_left
    if left >= per_tema:
        return (
            f"Пробного лимита хватит ещё на {_articles_accusative(left)}, а утверждение "
            f"запустит {exc.articles}. Уберите лишние Темы (🗑) и утвердите снова."
        )
    if left:
        remains = _plural(left, "осталась", "осталось", "осталось")
        text = f"Пробный лимит почти исчерпан: {remains} {articles_text(left)} — меньше одной Темы."
        return _with_contact(text, exc.contact)
    return limit_exhausted_text(exc)


def schedule_skipped_text(exc: TrialLimitExhausted) -> str:
    """The Расписание tick's one message a week when the Лимит of Планов is used up."""
    return (
        f"🕘 По Расписанию пора составить новый План, но его не будет.\n{limit_exhausted_text(exc)}"
    )


# --- Главное меню, Экран Настроек, Расписание (#95) ---
# Per-Настройка texts (question, example, confirmation) live next to their entry in
# `settings_screen.SETTING_FIELDS`, so adding a Настройка stays one entry; these are the
# shared ones.

BACK_BUTTON = "◀ Назад"
CANCEL_BUTTON = "Отмена"

WELCOME_TEXT = (
    "👋 Добро пожаловать! Я составляю для вас План Тем на неделю "
    "и пишу по нему Статьи для Дзена и VC.ru."
)
# /start in a group: the bot works in the private chat only (ADR-0015).
# ADR-0015: past `SIGNUP_DAILY_LIMIT` new Пространства in 24 hours.
SIGNUP_CLOSED_TEXT = "Регистрация временно закрыта. Попробуйте нажать /start завтра."
SIGNUP_LIMIT_ADMIN_TEXT = (
    "⚠️ Регистрация закрыта. Новых Пространств за сутки: {limit} (SIGNUP_DAILY_LIMIT). "
    "Новые /start получают «Регистрация временно закрыта», у существующих всё работает. "
    "Следующее такое уведомление — не раньше чем через сутки."
)
GROUP_START_TEXT = "Я работаю только в личном чате: откройте его и нажмите /start."
OPEN_PRIVATE_CHAT_BUTTON = "💬 Открыть личный чат"
# A message before the first /start (or from before the Пространства, #118).
NO_WORKSPACE_TEXT = "👋 Нажмите /start — я всё подготовлю."
# `/admin` (ADR-0015): metadata and spend only - never a Тема, a Статья or a Настройка.
ADMIN_TITLE = "🛠 Пространства: {shown} из {total}, недавние первыми"
ADMIN_EMPTY = "🛠 Пространств пока нет."
ADMIN_WORKSPACE = (
    "#{id} {username} · с {created} · активность {active}\n"
    "Планы {plans_used}/{plan_limit} · Статьи {articles_used}/{article_limit}{custom}\n"
    "30 дней: {spend}"
)
ADMIN_CUSTOM_LIMIT = " (свой Лимит)"
ADMIN_NO_USERNAME = "без username"
ADMIN_NO_ACTIVITY = "—"
ADMIN_NO_SPEND = "расхода нет"
ADMIN_UNITS = {
    "tokens": "ток.",
    "image": "карт.",
    "search_request": "поиск.",
    "wordstat_request": "Wordstat",
}
ADMIN_COST = "≈ {cost:.2f} ₽"
ADMIN_COST_INCOMPLETE = " (неполная)"
ADMIN_HINT = (
    "Задать Лимит: /admin limit <id> <планы> <статьи>, например /admin limit 12 2 12. "
    "Вернуть Лимит по конфигу: /admin limit <id> default"
)
ADMIN_LIMIT_SET = "✅ Лимит Пространства #{id}: Планов {plans}, Статей {articles}."
ADMIN_LIMIT_DEFAULT = "✅ Лимит Пространства #{id} снова по конфигу."
ADMIN_LIMIT_NOT_FOUND = "Пространства #{id} нет."
# The Главное меню's text (#149): the week, the next Plan, the Лимит and the Ниша.
MENU_TITLE = "🏠 Главное меню"
MENU_WEEK = "📋 Эта неделя ({week})"
MENU_WEEK_NONE = "📋 Плана на эту неделю пока нет"
MENU_WEEK_PENDING = "📋 Эта неделя: План ждёт вашего утверждения ({topics})"
MENU_WEEK_MORE = " и ещё {count}"
MENU_WEEK_ARTICLES = "Статьи: готово {ready} из {total} · 👍 отмечено {exported}"
MENU_ACCESS_TRIAL = "🎁 Пробный доступ"
# A Лимит an administrator raised (`/admin`): no longer a trial.
MENU_ACCESS_CUSTOM = "🎁 Доступ"
MENU_ACCESS_OVER = "закончился"
MENU_LIMIT_NOTE = (
    "Одна Статья = одна площадка (Дзен или VC.ru).\n«✏️ Доработать» тоже тратит одну Статью."
)
MENU_NICHE = "🎯 Ниша: {niche}"
MENU_NICHE_UNSET = "🎯 Ниша: не задана — укажите в ⚙️ Настройках"
MENU_PLAN_BUTTON = "📋 План недели"
MENU_TOPIC_BUTTON = "✍️ Предложить Тему"
MENU_HISTORY_BUTTON = "🗂 История"
MENU_SETTINGS_BUTTON = "⚙️ Настройки"
MENU_SCHEDULE_BUTTON = "🕘 Расписание"
OPEN_MENU_BUTTON = "🏠 Открыть меню"
# Under the История screens, which the menu edits in place (their own «◀ Назад» already
# means "previous page").
TO_MENU_BUTTON = "🏠 В меню"

HELP_TEXT = (
    "Я составляю для вас План Тем на неделю и пишу по нему Статьи для Дзена и VC.ru.\n\n"
    "Всё делается кнопками в меню — откройте его командой /menu:\n"
    "📋 План недели — где сейчас План и как составить новый\n"
    "✍️ Предложить Тему — добавить свою Тему в План\n"
    "🗂 История — прошлые Планы и готовые Статьи\n"
    "⚙️ Настройки — Ниша, Аудитория, Стиль автора, ваш канал или сайт\n"
    "🕘 Расписание — когда составляется новый План"
)
UNKNOWN_MESSAGE_TEXT = "Чтобы что-то сделать, откройте меню 👇"

PLAN_GENERATE_BUTTON = "🪄 Составить План"
PLAN_STATE_PENDING = "ждёт согласования"
PLAN_STATE_APPROVED = "утверждён"
PLAN_SENT_BELOW = "Прислал его ниже 👇"
# A Тема proposed into a Plan whose message is further up the chat: where to find it.
TOPIC_ADDED_TEXT = f"Тема добавлена в План. Открыть его: /menu → {MENU_PLAN_BUTTON}"
# What to do when a generated Plan came out empty (#84).
TOPIC_PROPOSE_HINT = f"Предложите Тему сами: /menu → {MENU_TOPIC_BUTTON}"
PLAN_EMPTY_TEXT = (
    "📋 План на {week} пока пуст: все Темы из него убраны. Можно предложить свою Тему."
)
# No Ниша told yet (#118): no Plan is drafted by the default one, and the Расписание tick waits.
NICHE_MISSING_TEXT = (
    "Сначала скажите, о чём ваш блог: без Ниши я не подберу Темы. "
    "Укажите её — после этого можно составить План."
)
NICHE_ASK_BUTTON = "✏️ Указать Нишу"
NICHE_UNSET = "не задана"
# The week's Plan is missing, but the Расписание won't make one either (the wizard isn't finished).
PLAN_MISSING_MANUAL_TEXT = "📋 Плана на {week} пока нет. Можно составить его прямо сейчас."
PLAN_MISSING_TEXT = (
    "📋 Плана на {week} пока нет.\n"
    "Новый План составляется автоматически по расписанию: {schedule}.\n"
    "Можно составить его прямо сейчас."
)

TOPIC_TOO_LONG_TEXT = "Тема слишком длинная — уложитесь в {limit} символов."
COMMENT_TOO_LONG_TEXT = (
    "⚠️ Комментарий слишком длинный — уложитесь в {limit} символов. "
    "Напишите его короче или нажмите «Пропустить»."
)
TOPIC_QUESTION = (
    "✍️ Напишите Тему — она добавится в План этой недели.\n\n"
    "Например: Как малому бизнесу посчитать окупаемость рекламы"
)
# Appended to every typed-input question (#88): the next message in the chat is the answer.
INPUT_HINT = "Напишите ответ следующим сообщением или нажмите «Отмена»."

SETTINGS_TITLE = "⚙️ Настройки\nДействуют на каждую следующую генерацию."
SETTINGS_CURRENT = "Сейчас: {value}"
SETTINGS_CHOOSE = "Выберите готовый вариант или задайте свой."
SETTINGS_SAVED = "✅ {text}"
SETTINGS_INVALID = "⚠️ {text}"
SETTINGS_TOO_LONG = "Слишком длинно — уложитесь в {limit} символов."

SCHEDULE_LABEL = "Расписание"
SCHEDULE_PURPOSE = "когда автоматически составляется новый План"
SCHEDULE_CHANGE_BUTTON = "🕘 Изменить Расписание"
SCHEDULE_TITLE = "🕘 Расписание\nНовый План составляется автоматически раз в неделю."
SCHEDULE_HOW = "Нажмите день недели, чтобы сменить его, «Изменить время» или «Часовой пояс»."
SCHEDULE_TIME_BUTTON = "🕐 Изменить время"
SCHEDULE_DAY_SAVED = "День изменён: {schedule}"
SCHEDULE_TIME_SAVED = "Время изменено: {schedule}"
SCHEDULE_TIME_QUESTION = (
    "🕐 Во сколько составлять новый План? Напишите время в формате ЧЧ:ММ.\n\nНапример: 09:30"
)
SCHEDULE_TIME_INVALID = "Не понял время «{text}». Нужно ЧЧ:ММ, например 09:30."
SCHEDULE_TIMEZONE_LINE = "Часовой пояс: {timezone}"
SCHEDULE_TIMEZONE_BUTTON = "🌍 Часовой пояс"
SCHEDULE_TIMEZONE_QUESTION = "🌍 Выберите часовой пояс: в нём считаются день и время Расписания."
SCHEDULE_TIMEZONE_SAVED = "Часовой пояс изменён: {timezone}"


def schedule_text(day: str, hour: int, minute: int) -> str:
    """«понедельник, 09:00» - the one way a schedule is shown on every screen."""
    return f"{weekday_name(day)}, {hour:02d}:{minute:02d}"


# --- Описание бота и онбординг (#96) ---
# Per-step questions, examples and validation come from `settings_screen.SETTING_FIELDS` - the
# wizard asks exactly what the Экран Настроек asks; these are only the wizard's own frame.

# setMyShortDescription (≤ 120) - the profile and the share card; setMyDescription (≤ 512) - the
# «What can this bot do?» card a new user sees before pressing «Начать».
BOT_SHORT_DESCRIPTION = (
    "Каждую неделю подбираю Темы по вашей Нише и пишу по ним Статьи для Дзена и VC.ru."
)
BOT_DESCRIPTION = (
    "Контент-план и Статьи для вашего блога.\n\n"
    "📋 Каждую неделю подбираю 3 Темы по вашей Нише с поводом «почему сейчас».\n"
    "✅ Вы согласуете его прямо здесь, в чате.\n"
    "✍️ По каждой Теме пишу Статьи для Дзена и VC.ru — с проверенными источниками.\n"
    "📄 Готовое читайте прямо в Telegram или скачивайте .docx/.md.\n\n"
    "Нажмите «Начать» — настройка займёт пару минут."
)

ONBOARDING_INTRO = (
    "👋 Здравствуйте! Я помогаю вести блог на Дзене и VC.ru.\n\n"
    "Каждую неделю я подбираю 3 Темы по вашей Нише — у каждой повод «почему сейчас» — и "
    "присылаю План. Когда вы его согласуете, пишу по каждой Теме Статьи с проверенными "
    "источниками.\n\n"
    "Настроим меня под вас: {count} — {steps}. Сразу после этого я составлю первый План.\n\n"
    "Выйти в меню можно в любой момент: /menu"
)
ONBOARDING_START_BUTTON = "▶ Начать настройку"
ONBOARDING_LATER_BUTTON = "Позже — открыть меню"
ONBOARDING_STEP_HEADER = "Шаг {number} из {total} · {label}\n↳ {purpose}"
ONBOARDING_SKIP_BUTTON = "Пропустить ⏭"
# First-run wording of each step's question (#96): the Экран Настроек asks to *change* a value
# («Напишите новую Нишу»), the wizard asks for it for the first time. The step header already
# says what the value drives, so these don't repeat it; the examples match the Экран Настроек.
ONBOARDING_NICHE_QUESTION = (
    "✏️ Какая у вас Ниша — о чём ваш блог? Напишите своими словами.\n\n"
    "Например: фитнес и здоровое питание"
)
ONBOARDING_AUDIENCE_QUESTION = (
    "✏️ Кто ваш читатель? Опишите его своими словами: кто он, что его беспокоит, чего хочет "
    "добиться и насколько разбирается в теме.\n\n"
    "Например: владельцы небольших кофеен и пекарен, 30–45 лет. Маркетингом занимаются сами, "
    "по вечерам. Боятся слить деньги на рекламу, не понимают, что работает. Хотят стабильный "
    "поток гостей без агентства. В терминах не разбираются — нужны простые шаги и примеры."
)
ONBOARDING_PERSONA_CHOOSE = (
    "Как мне понять ваш стиль? Проще всего — по вашим текстам: пришлите свои статьи или посты "
    "канала, и я сам опишу, как вы пишете. Или выберите готовый стиль кнопкой ниже, или "
    "опишите свой — «✏️ Свой стиль».\n\n{presets}"
)
# «Ваш канал или сайт» (#133): the owner's wording, with the formats the link may come in.
PROJECT_EXAMPLES = "Подойдёт: @my_channel · t.me/my_channel · example.ru · https://example.ru/blog"
ONBOARDING_PROJECT_QUESTION = (
    "✏️ Куда приглашать читателей в конце Статьи? Пришлите ссылку на ваш Telegram-канал или "
    "сайт — я аккуратно упомяну её в последнем абзаце, по теме Статьи.\n\n"
    f"{PROJECT_EXAMPLES}\n\n"
    "Можно через пробел добавить пару слов, что там. Нет канала — нажмите «Пропустить ⏭»."
)
ONBOARDING_REVIEW_TITLE = "🔎 Проверьте вводные"
ONBOARDING_REVIEW_HINT = (
    "Всё верно? Нажмите «🚀 Запустить» — я сразу составлю первый План. "
    "Что-то не так — поправьте кнопкой ниже."
)
ONBOARDING_LAUNCH_BUTTON = "🚀 Запустить"
# The first Plan is a Job: the Карта Ниши, Wordstat and one model call - a few minutes.
ONBOARDING_LAUNCHED = (
    "🚀 Запускаю! Подбираю Темы по вашей Нише — обычно это занимает несколько минут.\n\n"
    "План придёт сюда одним сообщением: в нём Темы можно заменить, убрать или утвердить, "
    "и по утверждённым я напишу Статьи. Дальше новый План будет приходить сам — {schedule}.\n\n"
    "Всё остальное — в меню: /menu"
)
ONBOARDING_ALREADY_LAUNCHED = (
    "✅ Вводные сохранены. Первый План уже составляется — он придёт сюда.\n\n"
    "Всё остальное — в меню: /menu"
)
ONBOARDING_CANCELLED = (
    "Хорошо, настройку можно продолжить в любой момент: /start\nВсё остальное — в меню: /menu"
)


def steps_count_text(count: int) -> str:
    """«5 коротких шагов», «3 коротких шага», «1 короткий шаг»."""
    return f"{count} {_plural(count, 'короткий шаг', 'коротких шага', 'коротких шагов')}"


# --- Стиль автора по образцам (#134) ---
# The Владелец sends Образцы - articles as a file, text or link, or a channel's posts - and the
# bot describes how they write (a Профиль стиля); nothing is used until «✅ Да, это я».

STYLE_ARTICLES_BUTTON = "📄 Пришлю статью"
STYLE_POSTS_BUTTON = "📨 Посты канала"
STYLE_PRESETS_BUTTON = "🎨 Готовый стиль"
STYLE_FROM_SAMPLES = "по вашим текстам"
"""How the Стиль автора reads on the Экран Настроек while a Профиль стиля is set."""

STYLE_ARTICLES_QUESTION = (
    "📄 Пришлите 1–3 свои статьи — по ним я пойму, как вы пишете:\n"
    "• файлом .docx, .txt или .md;\n"
    "• текстом — длинный можно несколькими сообщениями;\n"
    "• ссылкой на статью на сайте или на VC.ru.\n\n"
    "Статьи с Дзена по ссылке не открываются — пришлите их файлом или текстом. "
    "Сами тексты я не храню, только описание стиля.\n\n"
    "Когда всё отправите, нажмите «✅ Готово»."
)
STYLE_POSTS_QUESTION = (
    "📨 Перешлите сюда 5–10 постов своего канала или пришлите ссылку на него: t.me/имя_канала. "
    "Подойдёт только открытый канал.\n\n"
    "Из постов я возьму тон и лексику, а структуру Статьи построю сам. Сами посты я не храню.\n\n"
    "Нажимая «✅ Это мой канал — готово», вы подтверждаете, что канал ваш."
)
STYLE_DONE_BUTTON = "✅ Готово"
STYLE_POSTS_DONE_BUTTON = "✅ Это мой канал — готово"
STYLE_TALLY = "📥 Получено: {chars} знаков текста"
STYLE_TALLY_LINKS = ", ссылок: {links}"
STYLE_ENOUGH = "Этого достаточно — нажмите «Готово»."
STYLE_WARN_DZEN = "⚠️ Дзен не даёт читать статьи без входа — пришлите эту статью файлом или текстом."
STYLE_WARN_FILE_TYPE = "⚠️ Такой файл я не прочитаю — пришлите .docx, .txt или .md."
STYLE_WARN_FILE_BIG = "⚠️ Файл больше 1 МБ — пришлите статью покороче или текстом."
STYLE_WARN_FILE_UNREADABLE = "⚠️ Не получилось прочитать файл — пришлите статью текстом."
STYLE_WARN_LINKS = "⚠️ Хватит трёх ссылок — остальное пришлите файлом или текстом."
STYLE_WARN_UNSUPPORTED = "⚠️ Это не текст — пришлите статью файлом, текстом или ссылкой."
STYLE_NOTHING_YET = "Сначала пришлите текст, файл или ссылку."
STYLE_TOO_SHORT = (
    "⚠️ Маловато текста: {chars} знаков, а чтобы понять стиль, нужно хотя бы {minimum}. "
    "Пришлите ещё — или выберите готовый стиль."
)
STYLE_WORKING = "⏳ Читаю ваши тексты… Обычно это занимает до минуты."
STYLE_RESULT_TITLE = "Вот как вы пишете:"
STYLE_LOW_CONFIDENCE = (
    "Текста немного, поэтому описание приблизительное: его можно поправить или прислать ещё статей."
)
STYLE_UNREAD = (
    "Не получилось прочитать: {links}. Закрытые каналы и страницы со входом я не открываю — "
    "пришлите текст файлом или перешлите посты."
)
STYLE_CONFIRM_HINT = "Всё верно? После «✅» я буду писать Статьи так."
STYLE_OK_BUTTON = "✅ Да, это я"
STYLE_FIX_BUTTON = "✏️ Поправить"
STYLE_AGAIN_BUTTON = "🔄 Другие образцы"
STYLE_RESULT_TOO_SHORT = "😕 Не хватило текста: {chars} знаков, а нужно хотя бы {minimum}."
STYLE_FAILED = (
    "😕 Не получилось разобрать ваши тексты. Пришлите другие образцы или выберите готовый стиль."
)
STYLE_SAVED = "✅ Сохранил ваш стиль автора — новые Статьи я буду писать так.\n\n{summary}"
STYLE_FIX_QUESTION = (
    "✏️ Что поправить в описании? Напишите своими словами.\n\n"
    "Например: я обращаюсь к читателю на «ты» и почти не шучу"
)
STYLE_FIX_INVALID = "Напишите поправку текстом, не длиннее {limit} знаков."
STYLE_CORRECTIONS_LINE = "Ваши поправки: {corrections}"
STYLE_VOICE_EXAMPLES = "Так звучит ваш голос — эти отрывки я покажу модели как образец:"
STYLE_CONTINUE_BUTTON = "▶ Продолжить настройку"
STYLE_WARN_TELEGRAM_LINK = (
    "⚠️ Это ссылка на Telegram. Посты канала присылайте через «📨 Посты канала» — там же "
    "подтверждается, что канал ваш."
)
STYLE_WARN_FILE_SIZE_UNKNOWN = "⚠️ Не вижу размер файла — пришлите статью текстом."
STYLE_RESTARTED = (
    "⚠️ Я перезапускался и потерял то, что вы прислали раньше, — пришлите тексты ещё раз."
)
STYLE_DONE_WORDS = frozenset({"готово", "всё", "все", "готов", "done"})
"""Typed instead of pressing «✅ Готово» - taken as the button, not as an Образец."""
