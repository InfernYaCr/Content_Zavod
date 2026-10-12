"""texts: the Russian a content manager reads instead of the domain's English keys (#89).

Statuses, Площадка keys, `job_type` and day-of-week codes stay English in the DB and the
domain layer; the Telegram layer renders them through the lookups below. Every key lookup
falls back to the raw key, so a value added to the domain before it's added here still
renders - untranslated, but never a crash. Job failures and callback alerts deliberately
have no raw-key fallback: their technical text goes to the log, never to the chat.
"""

from __future__ import annotations

from datetime import date

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
HUB_PROGRESS = "⏳ В работе: готово {done} из {total}"
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
    never shown to the Владелец directly."""
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


def platform_name(platform: str) -> str:
    return PLATFORMS.get(platform, platform)


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
GROUP_START_TEXT = "Я работаю только в личном чате: откройте его и нажмите /start."
OPEN_PRIVATE_CHAT_BUTTON = "💬 Открыть личный чат"
# A message before the first /start (or from before the Пространства, #118).
NO_WORKSPACE_TEXT = "👋 Нажмите /start — я всё подготовлю."
# `/admin` (ADR-0015): for now only proves the Глобальный администратор is recognised; the
# list of Пространства and their Лимиты come with the Лимит itself.
ADMIN_PENDING_TEXT = (
    "Вы Глобальный администратор. Список Пространств, их расход и Лимиты появятся здесь "
    "вместе с Лимитом."
)
MENU_TEXT = "🏠 Главное меню\nВыберите, что сделать:"
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
PLAN_MISSING_TEXT = (
    "📋 Плана на {week} пока нет.\n"
    "Новый План составляется автоматически по расписанию: {schedule}.\n"
    "Можно составить его прямо сейчас."
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
    "📋 Каждую неделю подбираю Темы по вашей Нише и присылаю План.\n"
    "✅ Вы согласуете его прямо здесь, в чате.\n"
    "✍️ По каждой Теме пишу Статьи для Дзена и VC.ru — с проверенными источниками.\n"
    "📄 Готовое читайте прямо в Telegram или скачивайте .docx/.md.\n\n"
    "Нажмите «Начать» — настройка займёт пару минут."
)

ONBOARDING_INTRO = (
    "👋 Здравствуйте! Я помогаю вести блог на Дзене и VC.ru.\n\n"
    "Каждую неделю я подбираю Темы по вашей Нише и присылаю План. Когда вы его согласуете, "
    "пишу по каждой Теме Статьи с проверенными источниками.\n\n"
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
    "Выберите стиль кнопкой ниже или опишите свой — «✏️ Свой стиль».\n\n{presets}"
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
# The first Plan is a Job: a model call per Тема - usually a few minutes.
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
    if count % 10 == 1 and count % 100 != 11:
        return f"{count} короткий шаг"
    if 2 <= count % 10 <= 4 and not 12 <= count % 100 <= 14:
        return f"{count} коротких шага"
    return f"{count} коротких шагов"
