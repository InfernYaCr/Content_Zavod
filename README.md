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

Ещё не реализовано (см. открытые issues в трекере): деплой на VPS, обработка кнопок принятия/перегенерации готовой Статьи.

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

## Issue-трекер

GitHub Issues в этом репозитории, через `gh` CLI — см. [docs/agents/issue-tracker.md](docs/agents/issue-tracker.md) и [docs/agents/triage-labels.md](docs/agents/triage-labels.md).
