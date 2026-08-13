# Версионные миграции вместо schema-on-startup

## Начало

- Дата: 2026-08-14, ветка `feat/71-migration-runner`, поверх `master` (`25bba53`).
- Issue: [#71](https://github.com/InfernYaCr/Content_Zavod/issues/71).
- ADR: [0013 — версионные миграции вместо schema-on-startup](../adr/0013-versioned-migrations-replace-schema-on-startup.md).

## Цель задачи

- Заменить `ensure_schema()`-на-старте (каждый из 5 модулей сам накатывал свой `schema.sql`) на единый migration runner.
- Принять существующие таблицы как baseline, чтобы чистая установка и апгрейд сходились к одной схеме.
- Безопасно докатить `plans_one_pending_review_per_week_key` — партиальный уникальный индекс, который на установке с уже накопленными дублями `pending_review` Планов уронил бы старт процесса, если накатывать его вслепую.
- Явно исключено из scope: сама схема outbox/provenance-cost (следующие тикеты, которые этот раскрывает) — здесь только инфраструктура runner'а и одна миграция, чинящая уже выявленный риск.

## Что проделано

- `src/content_zavod/migrations/runner.py`: `MigrationRunner` — читает `.sql`-файлы из `migrations/sql/` по имени файла, применяет неприменённые, каждую в своей транзакции вместе со своей строкой в `schema_migrations` (упавшая на середине миграция не считается применённой); весь `run_pending()` держит Postgres advisory lock, потому что `bot` и `worker` вызывают его независимо на своём старте — без лока одновременный рестарт обоих гоняет два раннера за одной базой и второй падает на дубликате PRIMARY KEY.
- `src/content_zavod/migrations/sql/0001_baseline.sql`: пять прежних `schema.sql` (access, scheduling, job_queue, owner_settings, domain) один в один — все `IF NOT EXISTS`, идемпотентны — минус `plans_one_pending_review_per_week_key`.
- `src/content_zavod/migrations/sql/0002_dedupe_pending_review_plans.sql`: архивирует все Планы кроме самого нового `pending_review` на неделю (и их Темы), затем создаёт индекс.
- Удалены `ensure_schema()`/`_SCHEMA_SQL` и сами `schema.sql` в access/membership.py, access/join_requests.py, scheduling/settings_store.py, job_queue/queue.py, owner_settings/settings_store.py, domain/plan.py, domain/article.py.
- `entrypoints/bot.py` и `entrypoints/worker.py`: один вызов `run_migrations(pool)` сразу после создания пула, вместо семи разрозненных `ensure_schema()`.
- `tests/conftest.py`: сессионная фикстура `pool` теперь сама прогоняет миграции один раз; все `conftest.py`/тесты, звавшие `ensure_schema()` напрямую, обновлены.
- `tests/migrations/`: `conftest.py` — фикстура `isolated_pool` на отдельной Postgres-схеме того же testcontainer'а (через `server_settings={"search_path": ...}`), без второго контейнера на тест; `test_runner.py` — идемпотентность повторного прогона, индекс появляется на чистой установке, ключевой сценарий апгрейда с дублями (применяем только baseline к копии схемы, руками создаём два `pending_review` Плана на одну неделю — возможно только пока индекса нет, — прогоняем миграции, проверяем что апгрейд не падает, старый План архивируется вместе со своими Темами, новый остаётся `pending_review`, индекс появляется), гонка двух одновременных `run_pending()` (bot+worker restart) без падения на дубликате, и что задокументированный rollback `0002` не теряет данные (обе строки Планов остаются на месте после `DROP INDEX` + удаления строки из `schema_migrations`).
- `docs/adr/0013-...md`: почему свой runner вместо Alembic, baseline/апгрейд-инвариант, rollback (`DROP INDEX` + `DELETE FROM schema_migrations`, без потери данных — заархивированные дубли остаются архивными, не автовосстанавливаются), expand → deploy → backfill → contract.

## Что стало работать

- `bot`/`worker` при старте применяют только неприменённые миграции вместо повторного `CREATE TABLE IF NOT EXISTS` каждой из семи схем на каждом рестарте.
- Апгрейд установки с дублирующимися `pending_review` Планами на одну неделю проходит без ручного вмешательства: дубли архивируются, канонической остаётся самая свежая.
- Откат последней миграции задокументирован и проверен тестом (`test_rollback_of_0002_drops_only_the_index_without_losing_data`) — данные не теряются.
- Одновременный рестарт `bot` и `worker` (обычная форма деплоя) больше не может уронить один из них на гонке за `schema_migrations` — второй раннер ждёт advisory lock вместо падения на дубликате PRIMARY KEY.
- Ограничение: down-миграций (`.down.sql`) runner не поддерживает — откат `0002` описан в ADR как ручная операция (`DROP INDEX` + удаление строки из `schema_migrations`), это осознанный выбор, а не недоделка (см. ADR, раздел Rollback).

## Общее видение проекта после работы

- Схема БД больше не размазана по семи местам, которые каждый модуль применял сам; появление новой таблицы/индекса — это новый файл в `migrations/sql/`, а не правка `schema.sql` внутри модуля.
- Тикеты, добавляющие схему (outbox канонического сообщения, provenance/cost — см. контекст #71), теперь просто добавляют следующую пронумерованную миграцию поверх уже работающего runner'а.

## Схемы

Изменение — рефакторинг применения схемы плюс одна миграция с данными; не меняет lifecycle, очередь или trust boundary — по правилам `docs/standards/task-deliverable.md` схема не требуется.

## Проверка

- `uv run pytest -q` — 430 passed.
- `uv run ruff check .` — All checks passed.
- `uv run ruff format .` — все файлы отформатированы.
- Целевой прогон `uv run pytest tests/migrations -q` — 5 passed (идемпотентность, чистая установка, апгрейд с дублями, конкурентный старт двух раннеров, rollback без потери данных).

## Что осталось

- Down-миграции как формальный механизм runner'а не реализованы — при следующей миграции с более рискованным откатом стоит решить, нужен ли `.down.sql`, или ручной rollback per-ADR по-прежнему достаточен.
- `docs/reviews/2026-08-12-technical-audit.md` и `2026-08-12-audit-hardening-stage-1.md` продолжают ссылаться на старые пути `schema.sql` — это исторические снепшоты на момент аудита, не трогались намеренно.

## Rollback и эксплуатация

- Откат `0002`: `DROP INDEX plans_one_pending_review_per_week_key;` + `DELETE FROM schema_migrations WHERE version = '0002_dedupe_pending_review_plans';`. Заархивированные дубли остаются `archived` — без потери данных, без автовозврата в `pending_review` (см. ADR-0013).
- Откат `0001` не предусмотрен — это baseline существующих таблиц.
- Rollout: expand (`0001`+`0002` идемпотентно накатываются при старте) → deploy code (сам этот PR — переход на `run_migrations`) → backfill (совмещён с `0002`) → contract (следующий PR, который уберёт то, от чего больше никто не зависит).
