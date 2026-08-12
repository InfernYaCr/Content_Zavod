# Технический аудит Content Zavod

**Дата:** 12 августа 2026 года
**Режим:** read-only анализ без изменения production-кода
**Ревьюеры:** Code Review, Architecture Review, Content/DS Review
**База анализа:** `origin/master` на коммите `c388cf1`

## Резюме

Проект имеет хорошее базовое разделение на домен, очередь задач, pipelines, Telegram-слой и Yandex-интеграции. Долгие операции вынесены в отдельный worker, очередь использует PostgreSQL и `SKIP LOCKED`, а Версии Статей хранятся append-only.

При этом до production/VPS-релиза желательно закрыть несколько системных рисков. Наиболее важные из них:

1. полная Перегенерация недельного Плана фактически не запускает новую задачу;
2. долгую job могут одновременно выполнять два worker;
3. повторная обработка результата может создавать дубли Версий и Telegram-сообщений;
4. Источники генерируются моделью и проверяются только на доступность URL;
5. редактируемые Владелецем Голос и Ниша позволяют внедрять инструкции в prompt pipeline.

## Приоритет P0 — критические продуктовые риски

### 1. Источники не подтверждают утверждения Статьи

**Файлы:**

- `src/content_zavod/pipelines/article_pipeline.py:141-145`
- `src/content_zavod/pipelines/article_pipeline.py:201-229`

После написания Статьи та же модель генерирует список URL. Система оставляет все ссылки, которые отвечают как доступные, но не проверяет:

- относится ли страница к утверждению;
- подтверждает ли она цифру или юридический факт;
- дату и актуальность материала;
- авторитетность домена;
- соответствие конкретного утверждения конкретному Источнику.

**Сценарий:** модель пишет «рынок вырос на 37%», затем добавляет доступную главную страницу сайта. URL отвечает успешно, поэтому Статья получает формально живой, но нерелевантный Источник.

**Рекомендация:** перейти к контракту `claim → source`, проверять домен, дату и соответствие утверждения содержимому. До появления полноценной проверки неподтверждённые claims должны помечаться или блокироваться, а не публиковаться как подтверждённые.

### 2. Prompt injection через Голос, Нишу и промежуточные ответы

**Файлы:**

- `src/content_zavod/pipelines/plan_pipeline.py:63-70`
- `src/content_zavod/pipelines/plan_pipeline.py:127-135`
- `src/content_zavod/pipelines/article_pipeline.py:166-189`
- `src/content_zavod/telegram/voice_command.py:56-65`
- `src/content_zavod/telegram/niche_command.py:30-39`

Редактируемые Владелецем значения напрямую вставляются в system prompt. Предыдущая Версия, комментарий и сгенерированный outline также передаются следующим шагам как обычный текст без структурного отделения данных от инструкций.

**Сценарий:** значение Голоса содержит «игнорируй предыдущие правила и выводи только...». Эта строка становится частью системной инструкции и может изменить формат или назначение всей цепочки.

**Рекомендация:** ограничить размер пользовательских значений, отделять их в JSON/XML-полях, повторять неизменяемые правила после недоверенных данных и добавить corpus атак для offline eval.

## Приоритет P1 — высокая вероятность сбоя

### 3. Полная Перегенерация Плана не создаёт новую job

**Файлы:**

- `src/content_zavod/telegram/generate_plan_command.py:53-59`
- `src/content_zavod/domain/plan.py:193-200`
- `src/content_zavod/job_queue/queue.py:66-80`

Команда архивирует текущий План и снова ставит `generate_plan` с ключом `generate_plan:{week_label}`. Если первоначальная job уже имеет статус `done`, `ON CONFLICT` возвращает её ID, но не переводит обратно в `queued`.

**Результат:** старый План архивирован, новый не появляется.

**Рекомендация:** ввести generation/revision ID в payload и idempotency key либо отдельную безопасную операцию создания новой job для осознанной Перегенерации.

### 4. Истёкший lease позволяет выполнять одну job дважды

**Файлы:**

- `src/content_zavod/job_queue/queue.py:88-110`
- `src/content_zavod/job_queue/queue.py:171-190`
- `src/content_zavod/job_queue/worker.py:27-48`

`locked_at` задаётся только при захвате. Heartbeat или продление lease отсутствуют. Через десять минут `recover_stuck()` возвращает job в очередь, даже если первый worker продолжает выполнять длительную цепочку API-вызовов.

**Риски:** двойная стоимость, повторные внешние вызовы и гонка `complete/fail`.

**Рекомендация:** heartbeat, уникальный lease/claim token и условные `complete/fail`; альтернативно — жёсткий timeout задачи меньше lease с гарантированной отменой исполнения.

### 5. Failed job не переводит Статью в `error`

**Файлы:**

- `src/content_zavod/job_queue/worker.py:42-48`
- `src/content_zavod/entrypoints/bot.py:483-493`
- `src/content_zavod/domain/article.py:181-201`
- `src/content_zavod/domain/article.py:231-263`

При окончательной ошибке меняется только status job и отправляется уведомление. Статья остаётся `queued` или `regenerating`. Следующая Перегенерация может стать no-op, потому что lifecycle считает операцию уже выполняющейся.

**Рекомендация:** отдельный доменный failure handler, который по идентификатору job/generation атомарно переводит соответствующую Статью в `error`.

### 6. Повтор notification дублирует Версию Статьи

**Файлы:**

- `src/content_zavod/entrypoints/bot.py:511-524`
- `src/content_zavod/domain/article.py:181-201`
- `src/content_zavod/job_queue/notifications.py:35-56`

Handler сначала записывает Версию, затем отправляет Telegram-сообщение. Если Telegram упадёт после INSERT, повтор handler снова добавит ту же Версию.

Для `generate_plan` повтор также может создать повторное сообщение вместо обновления единственного каноничного сообщения Плана.

**Рекомендация:** уникальная связь `article_version ↔ source_job_id`, идемпотентный application receipt и отдельная Telegram-доставка с сохранённым `chat_id/message_id` для edit/upsert.

### 7. Решение заявки двумя Владельцами неатомарно

**Файлы:**

- `src/content_zavod/access/join_requests.py:94-115`
- `src/content_zavod/telegram/join_request_flow.py:72-87`

Сначала выполняется `SELECT pending`, затем безусловный `UPDATE`. Два Владельца могут одновременно выполнить approve и decline и запустить противоречивые side effects.

**Рекомендация:** один `UPDATE ... WHERE status='pending' RETURNING`; membership/уведомления выполняет только победившая транзакция.

### 8. Sensitive-режим неполон, особенно при Перегенерации

**Файлы:**

- `src/content_zavod/pipelines/article_pipeline.py:98-107`
- `src/content_zavod/pipelines/article_pipeline.py:140`
- `src/content_zavod/pipelines/article_pipeline.py:216-218`

Классификатор смотрит только на заголовок и Ключи. При Перегенерации summary/keywords теряются, а содержание прошлой Версии и комментарий не анализируются.

**Рекомендация:** классифицировать полный набор данных, добавить категории финансов, права, медицины и персональных данных. Целевой recall на тестовом корпусе — не менее 95%, parity обычной генерации и Перегенерации — 100%.

## Приоритет P2 — системные и качественные пробелы

### 9. Единственный активный План не обеспечен PostgreSQL

`src/content_zavod/domain/plan.py:154-190` выполняет `SELECT`, затем `INSERT`, но `src/content_zavod/domain/schema.sql:1-26` не содержит partial unique constraint для активного Плана недели.

Параллельные `/topic` и результат автогенерации могут создать два `pending_review` Плана. Нужны DB constraint и атомарный upsert/lock.

### 10. `retry()` принимает job любого статуса

`src/content_zavod/job_queue/queue.py:152-169` переводит в `queued` также `done` и `running` job. Повтор старой Telegram-кнопки способен запустить завершённую работу повторно. Требуется `WHERE id=$1 AND status='failed'` и отдельная обработка no-op/conflict.

### 11. Устаревшая Перегенерация Темы сообщает ложный успех

`src/content_zavod/domain/plan.py:292-303` не проверяет количество обновлённых строк, а `src/content_zavod/entrypoints/bot.py:504-510` всегда сообщает «Тема обновлена». При уже утверждённом или архивированном Плане результат теряется. Требуется typed stale-result error.

### 12. Trend scoring Wordstat недостаточно устойчив

`src/content_zavod/pipelines/plan_pipeline.py:90-101` и `188-194` сравнивают последнюю и первую точки без гарантированной сортировки, минимального объёма и проверки сезонности. Рост `1 → 2` может быть выбран вместо `1000 → 1500`.

Рекомендуются сортировка по дате, минимальный объём, slope/log-volume, доля растущих периодов и устойчивость к единичным выбросам.

### 13. Structured output фактически является свободным текстом

`src/content_zavod/pipelines/plan_pipeline.py:207-217` разбирает `Title/Summary/Keywords` и при повреждённом ответе молча подставляет значения. Malformed или обрезанный ответ может выглядеть успешным.

Рекомендуются JSON schema, строгая валидация, bounded retry и typed failure без silent fallback.

### 14. Отличия Дзена и VC не специфицированы

`src/content_zavod/pipelines/article_pipeline.py:166-198` передаёт только имя Площадки и общую просьбу учитывать её тон. Нет проверяемой матрицы аудитории, структуры, длины и CTA.

Рекомендуется rubric каждой Площадки и paired eval. Пример начального порога: cosine similarity между парой Статей одной Темы ниже `0.85`, плюс обязательные platform-specific признаки.

### 15. Provenance и стоимость неполны

**Файлы:**

- `src/content_zavod/pipelines/article_pipeline.py:125-153`
- `src/content_zavod/yandex/text_generator.py:68-88`
- `src/content_zavod/domain/types.py:78-86`
- `src/content_zavod/domain/schema.sql:43-51`

Сохраняются объединённые prompts, суммарные tokens и модель последнего шага. Не сохраняются temperature, max tokens, модель/usage каждого шага, версия prompt template и snapshot входов. Стоимость записывается как `0.0`.

**Результат:** Версию невозможно точно воспроизвести, сравнить или объяснить; runaway budget остаётся незаметным.

### 16. Scheduler не обеспечивает catch-up после рестарта

APScheduler использует in-memory job store. После остановки процесса пропущенное расписание исчезает, а при старте создаётся новый будущий cron.

Рекомендуется startup reconciliation текущей недели через идемпотентный `Plan.request_new` либо persistent scheduler store.

### 17. Нет версионных миграций и production runbook

Bot и worker выполняют `CREATE TABLE IF NOT EXISTS` при старте. Такой подход не применяет будущие `ALTER` к существующей БД. Отсутствуют зафиксированные health/readiness, backup/restore, rollback и диагностика зависших job.

Рекомендуются Alembic или аналогичные версионные миграции отдельным release step, а также systemd/container runbook для двух процессов.

## Минимальный offline eval-набор

Платные и live-запросы для этого набора не нужны.

1. `tests/evals/fixtures/topics.jsonl` — нормальные, перемешанные, неполные, шумные, малочастотные и сезонные Wordstat-ряды.
2. `tests/evals/fixtures/prompt_attacks.jsonl` — атаки через Голос, Нишу, комментарий и предыдущий контент.
3. `tests/evals/fixtures/topic_outputs.jsonl` — корректные и повреждённые structured outputs.
4. `tests/evals/fixtures/articles.jsonl` — атомарные claims, Источники и ожидаемая citation policy; `FakeUrlChecker`, без сети.
5. `tests/evals/fixtures/platform_pairs.jsonl` — синтетические пары Дзен/VC для проверки структуры и сходства.
6. JSON scorecard — единый результат pytest/eval.

Предлагаемые начальные метрики:

| Метрика | Gate |
|---|---:|
| Prompt injection attack success rate | `0%` |
| Malformed output acceptance | `0%` |
| Sensitive classifier recall | `≥95%` |
| Нерелевантные Источники, принятые системой | `0%` |
| Инвариантность ranking к порядку точек | `100%` |
| Provenance completeness | `100%` |
| Cross-platform cosine similarity | `<0.85` |

## Рекомендуемый порядок работ

1. Исправить Перегенерацию Плана и стратегию idempotency key.
2. Добавить heartbeat/fencing для job lease.
3. Сделать применение результатов и уведомления идемпотентными; исправить lifecycle `error`.
4. Закрыть prompt injection и ввести реальную проверку Источников.
5. Закрепить единственность активного Плана ограничением БД и исправить гонку заявок.
6. Внедрить строгий structured output и offline eval-набор.
7. Специфицировать различия Площадок и provenance/cost accounting.
8. Добавить миграции, startup reconciliation и VPS runbook.

## Выполненные проверки и ограничения

- Проведены три независимых read-only обзора: code, architecture и content/DS.
- Критические findings повторно проверены по production-коду.
- Live Telegram, Yandex и LLM API не вызывались.
- Файлы production-кода не изменялись.
- Полный pytest не запускался: `uv` и Python launcher не были доступны агентам через `PATH`.
- Findings относятся к основному коду, существовавшему в `origin/master`; текущая ветка добавляет только конфигурации агентов и этот отчёт.
