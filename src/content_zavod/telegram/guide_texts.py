"""guide_texts: the Russian of the Инструкция (#114, #152) - the short start in 3 steps, its
question buttons and the answer each one opens.

Kept apart from `texts.py` because it is prose rather than labels. Where an answer names a
button or a Настройка it uses the bot's own constants (`texts.py`, `SETTING_FIELDS`), filled in
by `guide.py`, so a renamed button is renamed in the Инструкция too. An answer is one short
screen; a «👍 / 👎» example only where it helps.
"""

from __future__ import annotations

GUIDE_BUTTON = "📖 Как пользоваться"
GUIDE_TO_QUESTIONS_BUTTON = "◀ К вопросам"
GUIDE_TO_MENU_BUTTON = "🏠 В меню"

# --- The first screen (owner-approved wording, #152) ---
GUIDE_START = (
    "Как это работает — в 3 шага:\n"
    "1️⃣ Раз в неделю я присылаю План: 3 Темы по вашей Нише.\n"
    "2️⃣ Вы нажимаете «Утвердить всё» — я пишу Статьи для Дзена и VC.ru.\n"
    "3️⃣ Открываете Статью, читаете, скачиваете Word и публикуете.\n\n"
    "Что непонятно? Нажмите кнопку ниже."
)

# --- The question buttons, each also its answer's title ---
GUIDE_WEEK_BUTTON = "🗓 Как проходит неделя"
GUIDE_ARTICLE_BUTTON = "📄 Что делать со Статьёй"
GUIDE_NICHE_BUTTON = "🎯 Ниша и Аудитория"
GUIDE_STYLE_BUTTON = "🎙 Стиль автора"
GUIDE_LINK_BUTTON = "🔗 Ссылка в конце Статьи"
GUIDE_SCHEDULE_BUTTON = "🕘 Расписание"
GUIDE_LIMIT_BUTTON = "🎁 Лимит"
GUIDE_FAQ_BUTTON = "❓ Частые вопросы"

# --- The answers ---
GUIDE_WEEK = (
    "1. По Расписанию приходит План: 3 Темы, под каждой — почему она сейчас.\n"
    "2. {regenerate} — заменить Тему, {delete} — убрать, «{approve_all}» — писать Статьи.\n"
    "3. Это же сообщение становится Хабом: ⏳ пишется, ✅ готово, ❌ не вышло — «{retry}».\n"
    "4. Нажмите Тему, затем «{article}»."
)
GUIDE_ARTICLE = (
    "Хаб → Тема → «{article}». Дальше:\n"
    "{read} — весь текст\n"
    "{docx} — файл для Дзена или VC.ru\n"
    "{refine} — одной строкой, что исправить\n"
    "{approve} — в Хабе Статья станет 👍"
)
GUIDE_NICHE = (
    "Ниша — о чём ваш блог. По ней я подбираю Темы.\n"
    "👍 «{niche_good}» 👎 «бизнес» — слишком широко.\n\n"
    "Аудитория — кто читатель и что его беспокоит.\n"
    "👍 «владельцы кофеен, боятся слить деньги на рекламу» 👎 «ЦА 25–45» — это ни о ком.\n\n"
    "Поменять: {where}"
)
GUIDE_STYLE = (
    "{purpose}. Проще всего — «{articles}» или «{posts}»: я сам опишу, как вы пишете, "
    "и покажу на проверку. Или «{presets}».\n"
    "👎 «Роль: эксперт» — так звучит любой текст из интернета.\n\n"
    "Поменять: {where}"
)
GUIDE_LINK = (
    "В последнем абзаце Статьи я по теме упомяну ваш канал или сайт — одна ссылка, без "
    "рекламы.\n"
    "👍 «@my_channel Канал о маркетинге для малого бизнеса»\n"
    "👎 «Подписывайтесь на наш канал!» — без ссылки читателю некуда идти.\n\n"
    "Поменять: {where}. Убрать ссылку — отправьте «-»."
)
GUIDE_SCHEDULE = (
    "Раз в неделю в это время я сам присылаю новый План — сюда, в этот чат.\n"
    "Пока не поменяете — {default}.\n"
    "День, время и часовой пояс: {where}\n"
    "Когда придёт следующий План — видно в {menu}."
)
GUIDE_LIMIT = (
    "Пробный доступ: {plans} и {articles}.\n"
    "Одна Статья = одна площадка: Тема для Дзена и VC.ru — это 2 Статьи.\n"
    "«{refine}» тоже тратит одну Статью. Замена Темы ({regenerate}) — бесплатно.\n"
    "Сколько осталось — в {menu}."
)
GUIDE_LIMIT_CONTACT = "\nНужно больше — напишите {contact}."
GUIDE_FAQ = (
    "Тема не нравится? {regenerate} у её номера в Плане — пришлю другую.\n"
    "Статья не та? «{refine}» — и одной строкой, что исправить.\n"
    "Где План? {menu} → {plan}.\n"
    "Где прошлые Статьи? {menu} → {history}.\n"
    "«{no_evidence}»? Проверенных фактов я не нашёл — сверьте цифры сами перед публикацией.\n"
    "Своя идея? {menu} → {topic}."
)
