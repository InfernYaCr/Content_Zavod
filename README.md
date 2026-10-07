# Content Zavod

Генерация недельных контент-планов и статей для медиаплощадок (Дзен, VC) для ниши маркетинга, с рабочим процессом через Telegram-бота для команды контент-менеджеров.

Домен и терминология — [CONTEXT.md](CONTEXT.md). Архитектурные решения и их обоснование — [docs/adr/](docs/adr/).

## Статус

MVP в разработке. Реализовано:

- **Job Queue** (`src/content_zavod/job_queue/`) — очередь задач на Postgres: идемпотентный `enqueue`, атомарный захват задачи воркером, ретраи с бэкоффом, уведомления о результате.
- **Yandex API клиенты** (`src/content_zavod/yandex/`) — `TextGenerator` (YandexGPT), `ImageGenerator` (YandexART), `KeywordStats` (Wordstat через Yandex Search API).
- **Доменный слой** (`src/content_zavod/domain/`) — `Plan`/`Article` и их жизненный цикл.
- **Job Handlers** (`src/content_zavod/pipelines/`) — `generate_plan`/`generate_article`/`regenerate_article`/`generate_cover`/`regenerate_topic`.
- **Telegram-слой** (`src/content_zavod/telegram/`) — `TelegramGateway`, `PlanReview` (согласование плана кнопками), `/topic` (ручное предложение Темы).
- **Membership** (`src/content_zavod/access/`) — allowlist Telegram-id с ролями Owner/Content-manager.
- **Планировщик** (`src/content_zavod/scheduling/`) — еженедельный триггер `generate_plan`.
- **Точки входа** (`bot_main.py`, `worker_main.py`) — два процесса по ADR-0004, см. «Локальный запуск» ниже.

Ещё не реализовано (см. открытые issues в трекере): деплой на VPS.

## Локальный запуск

Два отдельных процесса (ADR-0004): `bot_main.py` (Telegram + планировщик) и `worker_main.py` (разбор очереди задач).

1. `cp .env.example .env` и заполнить: токен тестового Telegram-бота (`@BotFather`), `TELEGRAM_NOTIFY_CHAT_ID`, DSN локального/тестового Postgres (`POSTGRES_DSN`), Yandex Cloud `YANDEX_FOLDER_ID` и один из `YANDEX_API_KEY`/`YANDEX_OAUTH_TOKEN`.
2. Поднять Postgres (например, `docker run -p 5432:5432 -e POSTGRES_PASSWORD=postgres postgres:16-alpine`) и указать его DSN в `.env`.
3. В двух терминалах:
   ```bash
   uv run python bot_main.py
   uv run python worker_main.py
   ```
4. Зарегистрировать свой `telegram_id` в allowlist (пока без отдельной команды/CLI — напрямую через Membership):
   ```bash
   uv run python -c "
   import asyncio, asyncpg
   from content_zavod.access import Membership
   from content_zavod.config import load_settings

   async def main():
       settings = load_settings()
       pool = await asyncpg.create_pool(dsn=settings.postgres_dsn)
       membership = Membership(pool)
       await membership.ensure_schema()
       await membership.add_member(123456789, 'owner')  # замените на свой telegram_id
       await pool.close()

   asyncio.run(main())
   "
   ```
5. Проверить: бот отвечает незарегистрированному `telegram_id` отказом; после регистрации — `/topic <текст>` кладёт Тему в План и присылает её с кнопками; воркер разбирает `generate_plan`/`regenerate_topic`/`generate_article`/`generate_cover` из очереди и результат приходит в `TELEGRAM_NOTIFY_CHAT_ID`.

## Разработка

Зависимости и виртуальное окружение — через [uv](https://docs.astral.sh/uv/):

```bash
uv sync
```

Тесты — на реальном Postgres через `testcontainers`, нужен запущенный Docker:

```bash
uv run pytest
```

### Проверки качества

После клонирования один раз установите Git hooks:

```bash
uv sync --all-groups --frozen
uv run pre-commit install
```

Локальный прогон тех же проверок, что выполняет CI:

```bash
uv run --frozen ruff check .
uv run --frozen ruff format --check .
uv run --frozen pytest
uv run pre-commit run --all-files
```

### Картинки Инструкции

Слайды «📖 Как пользоваться» и картинки к приветствиям лежат в `src/content_zavod/assets/guide/` и
закоммичены: бот просто отправляет PNG и браузер ему не нужен. Telegram `file_id` загруженной
картинки кэшируется в `owner_settings` вместе с хэшем файла, так что изменённый PNG бот загрузит
заново сам.

- Слайды (1280×960) — по ключу слайда из `telegram/guide.py`: `about`, `week`, `niche`, `audience`,
  `persona`, `directions`, `project`, `schedule`, `article`, `roles`, `faq`.
- Баннеры (1280×640): `welcome` (/start), `onboarding` (вступление Онбординга), `cm_welcome`
  (приветствие Контент-менеджера), `team_note` (закреплённая памятка «📌 Как мы работаем»).

Слайды — макеты экрана Telegram из настоящих текстов бота (`texts.py`, `SETTING_FIELDS`, экраны
Онбординга), поэтому после правки текстов их стоит пересобрать одной командой (Chromium от
Playwright: из `PLAYWRIGHT_BROWSERS_PATH` или `uv run --with playwright==1.56.0 playwright install chromium`):

```bash
uv run --with playwright==1.56.0 python scripts/render_guide_assets.py
# только некоторые и с HTML для правки в браузере:
uv run --with playwright==1.56.0 python scripts/render_guide_assets.py --only welcome,niche --html /tmp/guide-html
```

Рядом лежит `sources.json` — хэши текстов и разметки, из которых нарисован каждый PNG. Тест
`tests/telegram/test_guide.py` пересчитывает их и падает, если текст бота поменяли, а картинку не
пересобрали.

Дизайнер может просто заменить любой PNG: имя файла и размер сохранить, код не трогать. Если
после этого меняется текст, нарисованный на картинке, — поправить PNG и обновить только хэши:
`uv run python scripts/render_guide_assets.py --sources-only`.

## Issue-трекер

GitHub Issues в этом репозитории, через `gh` CLI — см. [docs/agents/issue-tracker.md](docs/agents/issue-tracker.md) и [docs/agents/triage-labels.md](docs/agents/triage-labels.md).
