# Content Zavod

Генерация недельных контент-планов и статей для медиаплощадок (Дзен, VC) для ниши маркетинга, с рабочим процессом через Telegram-бота для команды контент-менеджеров.

Домен и терминология — [CONTEXT.md](CONTEXT.md). Архитектурные решения и их обоснование — [docs/adr/](docs/adr/).

## Статус

MVP в разработке. Реализовано:

- **Job Queue** (`src/content_zavod/job_queue/`) — очередь задач на Postgres: идемпотентный `enqueue`, атомарный захват задачи воркером, ретраи с бэкоффом, уведомления о результате.
- **Yandex API клиенты** (`src/content_zavod/yandex/`) — `TextGenerator` (YandexGPT), `ImageGenerator` (YandexART), `KeywordStats` (Wordstat через Yandex Search API).
- **Telegram-слой** (`src/content_zavod/telegram/`) — `TelegramGateway` (отправка сообщений/файлов) и `PlanReview` (согласование плана кнопками).

Ещё не реализовано (см. открытые issues в трекере): доменный слой (План/Тема/Статья), пайплайны генерации, планировщик, деплой.

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
