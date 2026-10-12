# Content Zavod

Генерация недельных контент-планов и статей для медиаплощадок (Дзен, VC) через Telegram-бота. Бот открыт любому: каждый, кто нажал `/start`, получает своё Пространство — свои Настройки, Планы, Статьи и Расписание — и работает с ботом в личном чате (ADR-0015).

Домен и терминология — [CONTEXT.md](CONTEXT.md). Архитектурные решения и их обоснование — [docs/adr/](docs/adr/).

## Статус

MVP в разработке. Реализовано:

- **Job Queue** (`src/content_zavod/job_queue/`) — очередь задач на Postgres: идемпотентный `enqueue`, атомарный захват задачи воркером, ретраи с бэкоффом, уведомления о результате.
- **Yandex API клиенты** (`src/content_zavod/yandex/`) — `TextGenerator` (YandexGPT), `ImageGenerator` (Alice AI ART через Images API; включается `YANDEX_COVERS=on`), `KeywordStats` (Wordstat через Yandex Search API).
- **Доменный слой** (`src/content_zavod/domain/`) — `Plan`/`Article` и их жизненный цикл.
- **Job Handlers** (`src/content_zavod/pipelines/`) — `generate_plan`/`generate_article`/`regenerate_article`/`generate_cover`/`regenerate_topic`.
- **Telegram-слой** (`src/content_zavod/telegram/`) — `TelegramGateway`, `PlanReview` (согласование плана кнопками), `/topic` (ручное предложение Темы).
- **Пространства** (`src/content_zavod/workspaces/`) — одно на Telegram id, создаётся на `/start`; у каждой строки данных `workspace_id`.
- **Планировщик** (`src/content_zavod/scheduling/`) — раз в минуту ставит `generate_plan` каждому Пространству по его Расписанию и часовому поясу.
- **Точки входа** (`bot_main.py`, `worker_main.py`) — два процесса по ADR-0004, см. «Локальный запуск» ниже.

Запуск в Docker (на VPS или своём компьютере) — см. «Развернуть на VPS за 10 минут» ниже.

> **Пока нет пробного Лимита** (следующий шаг эпика #118), бот открыт любому без ограничений
> расхода: не давайте ссылку на бота посторонним.

## Локальный запуск

Два отдельных процесса (ADR-0004): `bot_main.py` (Telegram + планировщик) и `worker_main.py` (разбор очереди задач).

1. `cp .env.example .env` и заполнить: токен тестового Telegram-бота (`@BotFather`), DSN локального/тестового Postgres (`POSTGRES_DSN`), Yandex Cloud `YANDEX_FOLDER_ID` и один из `YANDEX_API_KEY`/`YANDEX_OAUTH_TOKEN`. По желанию — свой Telegram id в `GLOBAL_ADMIN_TELEGRAM_IDS` (Глобальный администратор).
2. Поднять Postgres (например, `docker run -p 5432:5432 -e POSTGRES_PASSWORD=postgres postgres:16-alpine`) и указать его DSN в `.env`.
3. В двух терминалах:
   ```bash
   uv run python bot_main.py
   uv run python worker_main.py
   ```
4. Проверить: написать боту `/start` в личном чате — бот создаст Пространство и проведёт по Онбордингу; «🚀 Запустить» ставит `generate_plan`, воркер разбирает очередь (`generate_plan`/`regenerate_topic`/`generate_article`/`generate_cover`), и План приходит в этот же личный чат.

## Развернуть на VPS за 10 минут

Всё работает в Docker: три контейнера — база Postgres, бот и воркер. Они сами перезапускаются
после сбоя и после перезагрузки сервера, база хранится в отдельном томе Docker и переживает
обновления.

**Что понадобится**

- VPS с Ubuntu 22.04 или 24.04 (хватит 2 vCPU / 4 ГБ), доступ по SSH под `root`.
- Токен Telegram-бота от `@BotFather`.
- Yandex Cloud: id каталога и API-ключ сервисного аккаунта (или OAuth-токен).
- Свой Telegram id (число) — для `GLOBAL_ADMIN_TELEGRAM_IDS`; узнать можно, например, у бота
  `@userinfobot`.

> **Секреты — только в файле `.env` на сервере.** Не присылайте токены и ключи в чаты, не
> вставляйте их в issues и не коммитьте в git. Если секрет утёк — перевыпустите его
> (`@BotFather` → `/revoke`, новый ключ в Yandex Cloud) и поменяйте в `.env`.

**1. Подключиться к серверу** (с вашего компьютера; IP — из письма хостинга):

```bash
ssh root@IP_СЕРВЕРА
```

**2. Установить Docker** (официальный apt-репозиторий, по
[docs.docker.com](https://docs.docker.com/engine/install/ubuntu/)) — скопировать целиком:

```bash
sudo apt update
sudo apt install -y ca-certificates curl git
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc

sudo tee /etc/apt/sources.list.d/docker.sources <<EOF
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: $(. /etc/os-release && echo "${UBUNTU_CODENAME:-$VERSION_CODENAME}")
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
EOF

sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo docker run hello-world
```

Последняя команда должна напечатать «Hello from Docker!».

**3. Скачать проект:**

```bash
git clone https://github.com/InfernYaCr/Content_Zavod.git /opt/content-zavod
cd /opt/content-zavod
```

Дальше все команды — из папки `/opt/content-zavod`.

**4. Заполнить настройки:**

```bash
cp .env.example .env
chmod 600 .env
openssl rand -hex 24   # готовый пароль для базы — скопируйте его
nano .env
```

В `.env` заполнить:

- `TELEGRAM_BOT_TOKEN`;
- `GLOBAL_ADMIN_TELEGRAM_IDS` — свой Telegram id (через запятую, если администраторов несколько);
- `YANDEX_FOLDER_ID` и **один** из `YANDEX_API_KEY` / `YANDEX_OAUTH_TOKEN`;
- `POSTGRES_PASSWORD` — вставить пароль из `openssl rand -hex 24`. `POSTGRES_USER` и
  `POSTGRES_DB` можно оставить как есть. `POSTGRES_DSN` оставить пустым — в Docker адрес базы
  собирается сам.

Сохранить: `Ctrl+O`, `Enter`, выйти: `Ctrl+X`.

**5. Запустить:**

```bash
docker compose up -d --build
```

Первая сборка занимает пару минут. При старте бот и воркер сами создают и обновляют таблицы в
базе (миграции).

**6. Проверить:**

```bash
docker compose ps
```

Должно быть три строки — `postgres` (с пометкой `healthy`), `bot`, `worker` — все в состоянии
`Up`. Если `bot` или `worker` в `Restarting`, смотрите логи:

```bash
docker compose logs -f bot worker
```

В логах воркера должно быть `worker started`. Выйти из просмотра логов — `Ctrl+C` (бот при этом
продолжит работать). Частые ошибки: `Unauthorized` — неверный `TELEGRAM_BOT_TOKEN`;
`missing required environment variable` — не заполнена указанная переменная в `.env`. Поправить
`.env` и снова выполнить `docker compose up -d`.

**7. Начать:** напишите боту `/start` в личном чате. Бот сам создаст ваше Пространство и
проведёт по настройке. Регистрировать никого не нужно: каждый, кто нажал `/start`, получает своё
Пространство и видит только свои Планы и Статьи.

**Обновить до новой версии:**

```bash
cd /opt/content-zavod
./scripts/backup_db.sh
git pull
docker compose up -d --build
```

При переходе на личные Пространства (#118) перед `docker compose up` поправьте `.env`:
удалите строку `TELEGRAM_NOTIFY_CHAT_ID=…` (больше не читается) и добавьте
`GLOBAL_ADMIN_TELEGRAM_IDS=<ваш Telegram id>`. Если результаты приходили вам в личный чат, ваше
прежнее «командное» Пространство станет вашим личным, с Настройками и Планами. Если в группу —
оно останется в базе, но бот перестанет в него писать, а вы получите новое по `/start`.

**Бэкапы базы.** Вся ценность — в накопленных Планах и Статьях, поэтому бэкап делается каждую
ночь автоматически. Разово, вручную:

```bash
./scripts/backup_db.sh        # хранит 14 последних копий
./scripts/backup_db.sh 30     # или сколько нужно
```

Копии лежат в `/opt/content-zavod/backups/` (`content_zavod_<дата>.sql.gz`), старые удаляются
сами. Чтобы бэкап делался каждую ночь в 03:00, откройте `crontab -e` и добавьте строку:

```cron
0 3 * * * /opt/content-zavod/scripts/backup_db.sh 14 >> /var/log/content-zavod-backup.log 2>&1
```

Время от времени скачивайте свежий бэкап к себе (например, `scp root@IP_СЕРВЕРА:/opt/content-zavod/backups/<файл> .`)
— копия на том же сервере не спасёт, если пропадёт сам сервер.

**Восстановить базу из бэкапа** (текущее содержимое базы будет заменено копией):

```bash
docker compose stop bot worker
docker compose exec -T postgres sh -c 'dropdb -U "$POSTGRES_USER" "$POSTGRES_DB" && createdb -U "$POSTGRES_USER" "$POSTGRES_DB"'
gunzip -c backups/content_zavod_ДАТА.sql.gz | docker compose exec -T postgres sh -c 'psql -v ON_ERROR_STOP=1 -q -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
docker compose start bot worker
```

**Полезное:**

- После правки `.env` — `docker compose up -d` (контейнеры пересоздадутся с новыми
  настройками; `docker compose restart` новый `.env` не подхватывает).
- `docker compose stop` / `docker compose start` — остановить / запустить всё.
- `docker compose down` — удалить контейнеры; база остаётся в томе. **Никогда не добавляйте
  `-v`**: `docker compose down -v` безвозвратно удаляет базу.
- Логи хранятся с ротацией (до 5 файлов по 10 МБ на контейнер) и не забьют диск.

### Запуск на своём компьютере (Windows, Docker Desktop)

То же самое можно запустить на своём ПК, например чтобы попробовать до покупки VPS. **Бот
работает, только пока компьютер включён и Docker Desktop запущен.**

1. Установить [Docker Desktop](https://docs.docker.com/desktop/setup/install/windows-install/),
   запустить его и дождаться статуса «Engine running». В настройках Docker Desktop
   (Settings → General) включить «Start Docker Desktop when you sign in» — тогда после
   перезагрузки ПК бот поднимется сам.
2. Скачать проект: `git clone https://github.com/InfernYaCr/Content_Zavod.git` или на GitHub
   «Code → Download ZIP» и распаковать.
3. Открыть PowerShell в папке проекта (в Проводнике: правый клик по папке → «Открыть в
   терминале») и создать настройки:
   ```powershell
   copy .env.example .env
   notepad .env
   ```
   Заполнить так же, как в шаге 4 выше; для `POSTGRES_PASSWORD` придумайте длинный пароль из
   латинских букв и цифр.
4. Запустить и проверить:
   ```powershell
   docker compose up -d --build
   docker compose ps
   docker compose logs -f bot worker
   ```
   Что должно получиться — как в шаге 6 выше. Выйти из логов — `Ctrl+C`.
5. Написать боту `/start` в личном чате — он сам создаст ваше Пространство.
6. Остановить: `docker compose stop`, запустить снова: `docker compose start`. Обновление:
   `git pull`, затем `docker compose up -d --build`.

Бэкап на Windows — тем же скриптом из Git Bash (ставится вместе с Git for Windows; в Проводнике:
правый клик по папке проекта → «Open Git Bash here»): `bash scripts/backup_db.sh`. Восстановление
из бэкапа — те же команды, что выше, тоже в Git Bash (в PowerShell нет `gunzip`).

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
картинки кэшируется в `bot_state` вместе с хэшем файла, так что изменённый PNG бот загрузит
заново сам.

- Слайды (1280×960) — по ключу слайда из `telegram/guide.py`: `about`, `sourcing`, `week`,
  `niche`, `audience`, `persona`, `project`, `schedule`, `propose`, `article`, `faq`.
- Баннеры (1280×640): `welcome` (/start), `onboarding` (вступление Онбординга).

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
