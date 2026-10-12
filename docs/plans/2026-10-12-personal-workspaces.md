# План: личные Пространства (#118)

**Дата:** 12 октября 2026 года
**Решение:** ADR-0015 (`docs/adr/0015-personal-workspaces-shared-schema.md`)
**Эпик:** #118

Шесть PR в `master`, по порядку. Каждый оставляет бота рабочим. До PR 4 (Telegram) он работает как сегодня: одно «командное» Пространство на `TELEGRAM_NOTIFY_CHAT_ID`, Роли и заявки на месте (см. ADR-0015, «Промежуточное состояние»). Общие правила для всех PR:

- `ruff check`, `ruff format --check` и targeted `pytest` затронутых пакетов. Тесты на изоляцию — обязательно, это дефекты, которые не видно глазами (поток C1).
- Cross-tenant тест строится одинаково: два Пространства A и B с одинаковыми данными (одна неделя, одинаковые ключи). Действие от имени A по id объекта B должно дать «не найдено» и ничего не изменить у B. Общая фикстура `two_workspaces` появляется в PR 1 (`tests/conftest.py`).
- Тест `tests/test_tenant_sql.py` (PR 1) должен оставаться зелёным: новый SQL по таблицам с `workspace_id` без `workspace_id` не пройдёт CI.

## PR 1 — Миграция 0012 + домен и репозитории с `workspace_id`

**Файлы:** `migrations/sql/0012_personal_workspaces.sql`; новый пакет `workspaces/` (`Workspace`, `WorkspaceId`, `Workspaces.get_or_create/get/get_by_owner`); `domain/plan.py`, `domain/article.py`, `domain/generation_steps.py`, `domain/research.py`; `owner_settings/` → `WorkspaceSettingsStore` (таблица `workspace_settings`) и `BotStateStore` (таблица `bot_state`; её используют `telegram/asset_photos.py` и `telegraph/publisher.py`); `telegraph/publisher.py` — `footer_link(workspace_id)` и `ArticlePages` с `workspace_id`; `settings/service.py`, `settings/onboarding.py`; `scheduling/settings_store.py`; `telegram/pending_inputs.py`; в `job_queue/queue.py` и `job_queue/models.py` — только `enqueue`/`get_job`/`get_status`/`retry` с `workspace_id` и `workspace_id` в `ClaimedJob`/`JobResult`. Вызовы в `telegram/*` и `entrypoints/*` механически получают `workspace_id` командного Пространства.

**Готово, когда:**
- 0012 накатывается на пустую базу и на базу с данными 0011: данные удалены, схема — по таблице ADR-0015, последовательность `jobs.id` не сброшена. Тест — `tests/migrations/test_runner.py`. Перед выкаткой — прогон на восстановленном `pg_dump` (ADR-0015, «Rollback»).
- У каждого метода репозитория первый параметр — `workspace_id`. Есть `tests/test_tenant_sql.py` с поимённым списком исключений.
- Бот и воркер стартуют и работают в чате команды, как раньше.

**Обязательные cross-tenant тесты:**
- `Plan`: `get`/`get_hub`/`approve_all`/`delete_item`/`regenerate_item`/`request_cover` по id чужого Плана или Темы → не найдено, у B ничего не изменилось. `find_active` и `request_new` на одну неделю в A и B дают два разных Плана и две Задачи (ключ идемпотентности не склеивается). Уникальный `pending_review` на неделю — отдельно у A и у B.
- `Plan.recent_topic_titles(A)` не видит Тем B.
- `Article`: `get`/`list_versions`/`get_version`/`request_regeneration`/`mark_exported` по чужому id → не найдено. `mark_generation_failed(A, job_id_B)` → `None`. То же для `Plan.mark_cover_generation_failed`/`is_manual_cover_job` и `get_telegraph_path`/`set_telegraph_path`.
- `WorkspaceSettingsStore`: Ниша A не видна в B. `OnboardingState` A и B независимы.
- `PendingInputs`, `ScheduleSettings`, `TopicResearchStore` — то же.
- `JobQueue.retry(A, job_B)` → не найдено, Задача B не тронута. `enqueue` с одним ключом в A и B → две Задачи.
- Составные FK: `INSERT` Темы с `workspace_id` A в План B падает.
- `generation_steps`: `UPDATE`/`DELETE` падают (триггер).

## PR 2 — Очередь и воркер

**Файлы:** `job_queue/queue.py` (`claim_next`: не больше одной `running` на Пространство, по кругу через `last_job_claimed_at`), `job_queue/worker.py` (хендлер получает `workspace_id`), `pipelines/*` (сигнатуры хендлеров; Настройки, `recent_topic_titles`, `regenerate_*`-чтения — по `workspace_id` Задачи; Wordstat-запросы пишутся шагами `wordstat_request`), `pipelines/provenance.py` (`unit`/`quantity`), `domain/generation_steps.py` (запись `workspace_id`, `unit`, `quantity`; `usage_for(workspace_id, since)`), `entrypoints/worker.py`.

**Готово, когда:**
- Воркер берёт Задачи по кругу: при очереди A1..A6, B1 вторая взятая Задача — B1.
- Каждая строка `generation_steps` несёт `workspace_id`, `unit`, `quantity`. `cost` остаётся `NULL` без настроенной цены.

**Обязательные cross-tenant тесты:**
- Хендлер `generate_plan` для Задачи A читает Нишу, Направления и недавние Темы только A (фейковый `SettingsService`/`Plan` с данными A и B).
- `regenerate_article`/`regenerate_topic` с payload, указывающим на id B, в Задаче A → ошибка «не найдено», данные B не прочитаны.
- `on_attempt` пишет шаги с `workspace_id` Задачи, а не из payload.
- `claim_next` не берёт вторую Задачу Пространства, у которого уже есть `running`.

## PR 3 — Расписание на каждое Пространство

**Файлы:** `scheduling/weekly_plan_trigger.py` (тик раз в минуту по всем Пространствам вместо одного `CronTrigger`; `reconcile_weekly_plan` становится телом тика; пока Telegram не переведён, Пространство одно — командное), `scheduling/settings_store.py`, `workspaces/` (`set_timezone`), `telegram/settings_screen.py` (экран Расписания: пояс МСК−1…МСК+9; убрать зависимость от `scheduler`), `entrypoints/bot.py` (регистрация тика).

**Готово, когда:**
- Пространства с разными поясами и днями получают План в своё локальное время. Рестарт после дедлайна догоняет пропущенное на первом тике.
- Тик пропускает Пространства с `bot_blocked_at`, с незавершёнными Задачами, с уже активным Планом недели, без сохранённой Ниши и с идущим Онбордингом.

**Обязательные тесты:**
- Тик с фиксированным `now`: A (МСК, пн 09:00) и B (МСК+7, пн 09:00) — при `now` = пн 03:00 МСК срабатывает только B.
- Повторный тик в ту же неделю не ставит вторую Задачу; пустой результат (#84) не перезапускается.
- Изменение Расписания A не меняет срабатывание B.
- Пространство без Ниши (только что нажали `/start`) тик не трогает: План по Нише по умолчанию не ставится.

## PR 4 — Telegram: Пространство на каждое обращение

**Файлы:** `entrypoints/bot.py` (резолв `Workspace` по `from_user.id` на каждый апдейт; `/start` → `get_or_create`; доставка уведомлений на `owner_telegram_id` по `JobResult.workspace_id`; 403 → `bot_blocked_at`; в группах — только ответ на `/start`); `telegram/callback_dispatcher.py` (`require_role` → резолв по `(workspace_id, id)`, «Кнопка устарела»); `telegram/callback_codec.py` (минус `request_access`, `approve_join`, `decline_join`, `*_member`, `menu_members`; `ACTION_ROLE` удаляется); `telegram/main_menu.py`, `telegram/guide.py`, `telegram/guide_texts.py`, `telegram/texts.py`, `telegram/onboarding.py`, `telegram/commands.py` (меню без Ролей; `/admin` в scope Глобального администратора); `telegram/input_prompt.py`, `telegram/comment_gated_regeneration.py` (ветки для групп и ForceReply удаляются); `config.py` (минус `TELEGRAM_NOTIFY_CHAT_ID`, плюс `GLOBAL_ADMIN_TELEGRAM_IDS`); `.env.example`. Удаляются: `access/`, `telegram/join_request_flow.py`, `telegram/members_command.py`, `TeamNote`, слайд «Роли» и его картинка в `assets/guide/`, прокладка командного Пространства. Миграция `0013_drop_roles.sql`. README — правка строк про `TELEGRAM_NOTIFY_CHAT_ID` и чат команды; согласовать с PR по #8, если тот ещё открыт. **Мержится после PR 2**: до него воркер берёт командное Пространство из `TELEGRAM_NOTIFY_CHAT_ID`, которое этот PR удаляет из `config.py`.

**Готово, когда:**
- Новый пользователь: `/start` → Пространство → Онбординг → «🚀 Запустить» → План приходит ему в личку.
- Второй пользователь параллельно проходит то же самое и не видит ничего от первого.
- В коде нет `Membership`, `Role`, `TELEGRAM_NOTIFY_CHAT_ID`, `team_chat_id`.

**Обязательные cross-tenant тесты:**
- Каждая ветка `CallbackDispatcher`, принимающая id (Хаб, Статья, История, Выгрузка, `retry`, Перегенерация), с id из Пространства B от пользователя A → alert «Кнопка устарела», без вызовов домена с изменением. Таблично, по всем `Action` с id.
- Уведомление о Задаче B уходит в чат B, а не в чат A и не в чат последнего писавшего.
- Страница для чтения Статьи B выходит со ссылкой Проекта B.
- Ответ текстом (комментарий к Перегенерации, Настройка) от A не закрывает ожидание B.
- `/admin` от не-администратора — как неизвестная команда.

## PR 5 — Лимит и Учёт расхода для Глобального администратора

**Файлы:** новый модуль `workspaces/limits.py` (одна проверка под `FOR UPDATE` строки Пространства: нет незавершённых Задач, суточный потолок, `списано + запрошено ≤ Лимит`); `job_queue/queue.py` (`enqueue` и `retry` принимают соединение вызывающего); `domain/plan.py` (`request_new`, `request_replacement`, `approve_all`), `domain/article.py` (`request_generation`, `request_regeneration`), `telegram/callback_dispatcher.py` (`approve_all` и постановка Статей и обложек — одна транзакция с проверкой) — проверка в транзакции `enqueue`; `entrypoints/bot.py` — запись в `limit_charges` при применении результата; `workspaces/admin.py` (список Пространств, расход, `set_limits`); новый `telegram/admin_command.py`; `telegram/main_menu.py` (строка «Осталось: …»), `telegram/texts.py`; `config.py` (`TRIAL_PLAN_LIMIT`, `TRIAL_ARTICLE_LIMIT`, `JOBS_PER_DAY_LIMIT`, `LIMIT_CONTACT`); тик из PR 3 — обработка исчерпанного Лимита и одно уведомление в неделю.

**Готово, когда:**
- При пробном Лимите 1/8 пользователь получает один План и восемь Статей, считая «✏️ Доработать». Девятая Статья и второй План — отказ с контактом.
- `/admin` показывает Пространства, «списано/Лимит» и расход за 30 дней. `/admin limit` поднимает Лимит, и кнопки снова работают.

**Обязательные тесты:**
- Гонка: два одновременных `approve_all` при остатке на один — ставится ровно один набор.
- Упавшая Задача не списывает; «🔁 Повторить» и её успех списывают один раз; повторная доставка уведомления не списывает дважды (PK `job_id`); устаревший результат (`stale`) не списывает.
- Пока у Пространства есть незавершённая Задача, любая новая генерация получает «Дождитесь…» и ничего не ставит.
- Повторный callback «Утвердить всё» уже утверждённого Плана не считает поставленные Статьи второй раз.
- «Утвердить всё» при нехватке ничего не ставит и сообщает остаток.
- Cross-tenant: списания A не уменьшают остаток B; `/admin limit` для A не меняет B; Глобальный администратор не получает содержимого Тем и Статей.

## PR 6 — Безопасность: ввод и злоупотребления

**Файлы:** `settings/service.py`, `settings/persona.py`, `settings/project.py` (пределы длины по ADR-0015), `telegram/topic_command.py`, `telegram/comment_gated_regeneration.py` (пределы для Темы и комментария), `entrypoints/bot.py` (rate limit `/start`, `SIGNUP_DAILY_LIMIT` с уведомлением Глобального администратора), `config.py`.

**Готово, когда:**
- Слишком длинный ввод отвергается понятным текстом с пределом, ничего не сохраняется.
- Сверх `SIGNUP_DAILY_LIMIT` новые Пространства не создаются, у существующих всё работает.

**Обязательные тесты:**
- Таблица пределов: граница принимается, граница + 1 отвергается — для каждой Настройки, Темы и комментария.
- Частый `/start` от одного id создаёт одно Пространство и не отвечает чаще порога.
- Промпт генерации A с injection в Аудитории A не содержит Настроек и Тем B (снимок собранного промпта).

## Что можно вести параллельно

| После | Параллельно | Почему без конфликтов |
|---|---|---|
| PR 1 | **PR 2 ∥ PR 3** | PR 2 — `job_queue/` (кроме уже изменённых в PR 1 сигнатур), `pipelines/`, `entrypoints/worker.py`, `domain/generation_steps.py`. PR 3 — `scheduling/`, `workspaces/` (`set_timezone`), расписательная часть `telegram/settings_screen.py`, регистрация тика в `entrypoints/bot.py`. Общих файлов нет. |
| PR 2 и PR 3 | PR 4 | PR 4 удаляет `TELEGRAM_NOTIFY_CHAT_ID`, на котором до PR 2 держится воркер, и трогает `settings_screen.py` и `entrypoints/bot.py` после PR 3. |
| PR 4 | **PR 5 ∥ PR 6** | PR 5 — `workspaces/limits.py`, `workspaces/admin.py`, доменные методы постановки, `job_queue/queue.py`, тик. PR 6 — `settings/`, `telegram/topic_command.py`, `telegram/comment_gated_regeneration.py`. Оба добавляют по нескольку строк в `entrypoints/bot.py`, `config.py` и `telegram/texts.py`: конфликт тривиален, второй делает rebase. |

Расписание (PR 3) идёт до Telegram (PR 4), а не после: тик не зависит от Telegram-слоя, а без него PR 4 оставил бы в `master` cron-джоб без Пространства, и `SettingsScreen` упал бы на `reschedule_job`.

Итого по времени: PR 1 → (PR 2 ∥ PR 3) → PR 4 → (PR 5 ∥ PR 6). Перед первой выкаткой PR 1 — `pg_dump`: миграция 0012 удаляет текущие данные, и `bot` с `worker` останавливаются на время выкатки (ADR-0015, «Миграция»). С PR 4 бот открыт любому, а Лимит появляется только в PR 5. Поэтому между PR 4 и PR 5 `master` не выкатывается туда, где бота видят посторонние: разработка — локально, ссылку на бота никому не давать.
