# План: Telegram-бот АО «Узбекгидроэнерго» — приём обращений граждан и жалоб на коррупцию

## Context

АО «Узбекгидроэнерго» нужен Telegram-бот для приёма **обращений граждан** и **жалоб на коррупцию** (с возможностью **анонимной** подачи). Пользователь выбирает тип, заполняет форму (ФИО, телефон, текст, фото/документы), отправляет. Обращение сохраняется в БД с тикет-номером и доставляется **ответственным лицам** (пользователям бота с ролевыми флагами). Ответственный меняет статус (взять в работу / закрыть) и пишет ответ, который бот пересылает заявителю. После отправки заявитель получает «✅ Спасибо за ваше обращение». Требуется мультиязычность (5 локалей) и эмодзи для UX.

Проект новый (пустой каталог, только `docs/`). Стек и решения подтверждены с пользователем и сверены с эталонными production-шаблонами aiogram (andrew000/aiogram-template, wakaree/aiogram_bot_template, MasterGroosha, офиц. документация aiogram 3.27). План прошёл критическое ревью по трём направлениям (архитектура/aiogram, безопасность/приватность, UX/i18n); ключевые замечания учтены ниже.

### Подтверждённые решения
- **Стек:** Python 3.11+, aiogram 3.x, `aiogram_i18n` + Fluent (`FluentRuntimeCore`), PostgreSQL + SQLAlchemy 2.0 async (`asyncpg`) + Alembic, Redis (FSM + throttling), Docker.
- **Режим:** polling (тест), webhook (встроенный aiohttp `SimpleRequestHandler`) для прода — выбор по конфигу.
- **Языки (5):** `ru`, `kaa` (каракалпакский), `uz_cyrl` (Ўзбекча), `uz_latn` (Oʻzbekcha), `en`. Выбор при первом `/start`, смена в любой момент. Все переводы — осмысленные (не подстрочник), официально-деловой тон.
- **Роли:** `citizen` (по умолчанию), `responsible` с типами `appeal`/`corruption` (флаги в БД), `admin` (bootstrap из `.env`). Админ назначает ответственных командами бота.
- **Маршрутизация:** обращение приходит **всем** ответственным нужного типа; кто первый «взял в работу» — за тем закрепляется (атомарно), у остальных карточка **обновляется у всех**.
- **Анонимность (антикоррупционный канал):** для жалоб ФИО/телефон опциональны. tg_id анонима **не хранится открыто** — хранится зашифрованным ключом вне БД; ответ доставляется push после расшифровки в памяти. Дамп БД не деанонимизирует.
- **Реакция:** статусы (`new`/`in_progress`/`closed`) + текстовый ответ заявителю.
- **«Мои обращения»:** заявитель видит список своих обращений и статусы (анонимные жалобы в списке **не** показываются).
- **Аудит:** полный неизменяемый audit-trail (смена статусов, назначения ролей, просмотры/доступ).

## Структура проекта

```
bot/
  __main__.py              # точка входа: config → factory → polling|webhook, shutdown-хуки
  config.py                # pydantic-settings (.env, SecretStr), ADMIN_IDS, LOCALES, ANON_ENC_KEY
  factory.py               # create_bot/dispatcher/redis/session_pool/i18n; регистрация middleware в строгом порядке
  runners/
    polling.py             # delete_webhook + start_polling + graceful shutdown
    webhook.py             # aiohttp SimpleRequestHandler + setup_application + set_webhook(secret) + shutdown
  db/
    base.py                # DeclarativeBase
    models.py              # User, Submission, SubmissionAttachment, SubmissionResponse,
                           #   SubmissionDelivery, SubmissionStatusEvent, AuditLog, TicketCounter + Enums(native_enum=False)
    session.py             # create_async_engine + async_sessionmaker
    repositories.py        # UserRepo, SubmissionRepo (атомарный захват, генерация ticket), AuditRepo
  security/
    crypto.py              # шифрование/дешифрование anon chat-ref (Fernet/AES-GCM, ключ из config)
    logging.py             # PII-safe форматтер/фильтр логов (не печатать text/ФИО/телефон/tg_id)
  middlewares/
    db.py                  # DbSessionMiddleware (OUTER, одна сессия на апдейт)
    user.py                # UserMiddleware (OUTER, get-or-create User, до i18n и фильтров)
    i18n_manager.py        # SQLAlchemyManager(BaseManager) — локаль из User.language
    throttling.py          # ThrottlingMiddleware (OUTER, Redis-based, обязателен)
    media_group.py         # агрегация альбомов по media_group_id (дебаунс-буфер)
  filters/
    roles.py               # IsAdmin, IsResponsible(type) + object-level проверка прав на submission
  handlers/
    __init__.py            # агрегация роутеров; cancel-роутер с StateFilter('*') выше форм
    start.py               # /start → выбор языка → главное меню; set_my_commands по локалям
    language.py            # смена языка (запрещена внутри формы — подсказка завершить/отменить)
    submission.py          # FSM-форма обращения/жалобы (назад/отмена/валидация/альбомы)
    my_submissions.py      # «Мои обращения» (список + статусы; без анонимных)
    responsible.py         # реакция: атомарный захват, ответ (FSM), закрытие; обновление кнопок у всех
    admin.py               # /assign, /responsibles (строго IsAdmin на каждом шаге)
    errors.py              # глобальный error-handler (логирует БЕЗ тела апдейта/PII)
  keyboards/
    inline.py              # клавиатуры: язык, меню, реакция, навигация формы (CallbackData factory)
  services/
    submissions.py         # создание Submission (commit до рассылки), выбор ответственных,
                           #   per-recipient рассылка с обработкой Forbidden/RetryAfter, пересылка ответа,
                           #   enforcement анонимности, рендер с ЯВНОЙ локалью получателя
  utils/
    text.py                # split на части при >4096, экранирование пользовательского ввода
  locales/
    {ru,kaa,uz_cyrl,uz_latn,en}/LC_MESSAGES/bot.ftl
alembic/
  env.py                   # импорт моделей; async через run_sync; offline-ветка; DSN из config
  versions/
alembic.ini
docker-compose.yml         # postgres + redis + bot (+ migrate one-shot); healthchecks; порт бота не наружу
Dockerfile
pyproject.toml
.env.dist                  # шаблон БЕЗ значений; .env в .gitignore
Makefile
README.md
```

## Модель данных

> Enum-поля — `sa.Enum(..., native_enum=False)` (VARCHAR+CHECK), чтобы Alembic-миграции были обратимыми и автоген стабильным.

**User**
- `id` PK, `tg_id` BigInteger unique, `username`, `full_name`, `language` str nullable
- `is_admin`, `resp_appeal`, `resp_corruption` bool
- `created_at`

**Submission**
- `id` PK, `public_id` str unique — **несеквенциальный** публичный идентификатор (рандом, напр. base32), показывается пользователю
- `ticket_number` str unique — внутренний `OBR-YYYY-NNNN`/`COR-YYYY-NNNN` (для документооборота)
- `type` Enum(`appeal`|`corruption`), `status` Enum(`new`|`in_progress`|`closed`) default `new`
- `is_anonymous` bool
- `author_user_id` FK→User **nullable** — заполняется ТОЛЬКО для неанонимных (для «Мои обращения»)
- `full_name`, `phone` nullable (принудительно NULL при анонимной — enforcement в сервисе)
- `text` Text
- `assigned_to_user_id` FK→User nullable
- `created_at`, `updated_at`, `closed_at` nullable, `closed_by_user_id` FK nullable

**AnonDeliveryRef** (только для анонимных; разрыв связи)
- `id` PK, `submission_id` FK unique, `enc_chat_ref` LargeBinary — зашифрованный tg_id (ключ `ANON_ENC_KEY` вне БД)
- Для неанонимных таблица не используется (push идёт по `author_user_id.tg_id`)

**SubmissionAttachment** (1→N)
- `id` PK, `submission_id` FK, `file_id` str, `file_type` Enum(`photo`|`document`), `order` int

**SubmissionResponse** (история ответов)
- `id` PK, `submission_id` FK, `responder_user_id` FK, `text` Text, `created_at`

**SubmissionDelivery** (доставка карточек ответственным — для обновления кнопок у всех + идемпотентность + аудит)
- `id` PK, `submission_id` FK, `responsible_user_id` FK, `chat_id` BigInteger, `message_id` BigInteger nullable, `delivered_at`

**SubmissionStatusEvent** (журнал смены статусов)
- `id` PK, `submission_id` FK, `actor_user_id` FK, `from_status`, `to_status`, `created_at`

**AuditLog** (неизменяемый аудит)
- `id` PK, `actor_user_id` FK nullable, `action` str (`assign_role`/`view_submission`/`status_change`/`reply`/…), `target` str, `meta` JSON (без PII), `created_at`

**TicketCounter** (атомарная генерация номеров)
- `type` + `year` PK, `counter` int — инкремент через `UPDATE … RETURNING` под блокировкой строки; сброс по годам

## Потоки

### Заявитель (citizen)
1. `/start` → если `language` null, inline-выбор языка (единообразно: 🇺🇿 для uz_cyrl/uz_latn/kaa или без флагов у всех — решить при реализации, см. UX) → сохранить.
2. Главное меню (inline): 📝 Обращение граждан · 🛡 Жалоба на коррупцию · 📋 Мои обращения · 🌐 Сменить язык.
3. FSM-форма (Redis storage, TTL на состояния):
   - Для `corruption`: «Подать анонимно?» (да/нет) + предупреждение, что вложения/EXIF могут раскрыть личность. При «да» → `is_anonymous=True`, шаги ФИО/телефон пропускаются, `author_user_id` НЕ сохраняется.
   - Шаги: `ФИО` → `Телефон` (кнопка `request_contact`) → `Текст` → `Вложения` (альбомы агрегируются; «Пропустить»/«Готово»; лимит кол-ва/размера) → **подтверждение** (сводка; для анонима «🕵 Анонимно»; «✅ Отправить»/«❌ Отменить»).
   - Навигация: «◀ Назад» с восстановлением состояния (учёт пропущенных шагов при анонимности) и сохранением введённого; «Отмена» с подтверждением на поздних шагах; глобальный `/cancel` (StateFilter('*')). С первого шага «Назад» → главное меню.
   - Валидация: непустой текст; телефон (мягкая, +998…/контакт); неожиданный тип контента → подсказка без сброса FSM; лимит длины текста (с учётом 4096).
4. После «Отправить»: сервис создаёт Submission (commit) + Attachments, атомарно генерит номер, для анонима пишет `AnonDeliveryRef(enc_chat_ref)`; **затем** (вне транзакции) рассылает ответственным. Заявителю: «✅ Спасибо! Номер: `<public_id>`…» на его языке.
5. Ответ ответственного → push заявителю «📩 Ответ по обращению `<public_id>`: …» на **языке заявителя** (для анонима — расшифровка ref в памяти).

### Ответственное лицо (responsible)
1. Сервис находит всех User с нужным флагом, шлёт каждому карточку **на его языке** (метки/кнопки — локаль ответственного; тело обращения — как написал гражданин), вложения через `send_media_group`/`send_photo`. Каждая доставка → `SubmissionDelivery(chat_id, message_id)`. Per-recipient try/except (`TelegramForbiddenError`/`TelegramRetryAfter`).
2. Inline-кнопки (`ReactionCb(action, submission_id)`): «🟡 Взять в работу» · «✍ Ответить» · «✅ Закрыть». На каждый клик — object-level авторизация (тип submission ∈ права пользователя; submission существует).
3. «Взять в работу» → **атомарный захват**: `UPDATE … SET status='in_progress', assigned_to=:uid WHERE id=:id AND status='new' RETURNING id`. Выиграл → у **всех** получателей (по `SubmissionDelivery`) `edit_message` → «В работе у …», кнопки скрываются. Проиграл → `answer_callback_query("Уже в работе у …")`. Событие в `SubmissionStatusEvent` + `AuditLog`.
4. «Ответить» → FSM(`ResponseForm`) текст → `SubmissionResponse` → пересылка заявителю (см. поток заявителя п.5).
5. «Закрыть» → `status=closed`, `closed_at/closed_by`, событие в журнал; опц. уведомление заявителю.

### Админ (admin)
- `ADMIN_IDS` из `.env` → при старте проставляется `is_admin`.
- `/assign` (строго IsAdmin на всех шагах) — по tg_id/пересланному + inline-выбор типа → флаг; запись в `AuditLog`.
- `/responsibles` — список, снятие флага (аудит).
- «Нет ответственных нужного типа» → Submission остаётся `new`, уведомление админам; заявителю текст «принято, в ближайшее время назначим» (без ложного «в работе»).

## Технические требования (учтённые из ревью)

- **Middleware-порядок (строго):** `DbSession (outer)` → `User get-or-create (outer)` → `i18n (aiogram_i18n setup, использует сессию того же апдейта)` → `Throttling (outer)` → роутеры/фильтры. Одна `AsyncSession` на апдейт, доступна фильтрам.
- **Конкурентность:** атомарный захват «взять в работу» (UPDATE…WHERE status='new' RETURNING) и атомарная генерация `ticket_number` (TicketCounter + ретрай на IntegrityError). **Конкурентные тесты — против реального Postgres** (testcontainers/локальный PG), не на SQLite.
- **Media group:** агрегация входящего альбома (`media_group_id`, дебаунс-буфер); caption первого элемента → как текст, если шаг это допускает. Отправка нескольких вложений — `send_media_group`.
- **Кросс-юзерная локализация:** сообщения заявителю/ответственным рендерятся с **явной локалью получателя** (`i18n.core.get(key, locale=...)`), НЕ из контекста отправителя.
- **Лимит 4096:** `utils/text.split` при показе сводки/карточки/ответа; ограничение длины ввода.
- **Анонимность (enforcement):** сервис принудительно зануляет ФИО/телефон и не пишет `author_user_id` при `is_anonymous`; `forward_message`/`copy_message` исходного сообщения анонима запрещены; вложения отправляются как новые сообщения по `file_id` (не forward). Опц. предупреждение про EXIF.
- **Логирование:** PII-safe форматтер; error-handler логирует `update_id`/тип/`public_id`, НЕ тело апдейта; запрет DEBUG-дампов апдейтов в проде; `SecretStr` не в логах.
- **Секреты:** `BOT_TOKEN`, пароли БД/Redis, `WEBHOOK_SECRET`, `ANON_ENC_KEY` — через `SecretStr`/`.env`; `.env` в `.gitignore`, в git только `.env.dist` с плейсхолдерами.
- **Webhook:** HTTPS обязателен (TLS на reverse-proxy), порт бота не публикуется наружу в compose, `WEBHOOK_SECRET` высокоэнтропийный, непредсказуемый путь, секрет через `X-Telegram-Bot-Api-Secret-Token`.
- **Throttling:** обязателен (Redis-based, per-user), лимиты на старт формы/отправку и на вложения (кол-во/размер).
- **Graceful shutdown:** `dp.shutdown` хуки — закрыть `bot.session`, `storage`, `engine.dispose()`, Redis-пул; webhook — остановить aiohttp-runner.
- **i18n:** `FluentRuntimeCore(path="bot/locales/{locale}/LC_MESSAGES")`, `default_locale="ru"`; null `language` — сигнал для UI выбора, не ошибка; Fluent-селекторы для плюрализации; даты — TZ Узбекистана (UTC+5).
- **Alembic:** `env.py` импортирует `models` (иначе пустая metadata); async через `connection.run_sync`; offline-ветка настроена; `compare_type/server_default=True`; миграции с `native_enum=False` обратимы; в проде миграции — отдельный one-shot job/init (не в каждой реплике).
- **FSM-устойчивость:** TTL на состояния; брошенные формы с PII не висят вечно; Redis запаролен и не наружу.

## Процесс реализации: TDD + волны + ревью

**Метод:** строгий TDD (red → green → refactor). Реализация — **волнами**; после каждой — **code-review параллельными агентами** (корректность, безопасность/приватность, конвенции aiogram). Замечания устраняются до следующей волны. В конце — **тотальное ревью**.

**Юнит-тесты:** репозитории, генерация `ticket_number` (+конкурентность на PG), атомарный захват (+конкурентность на PG), сервис submissions (создание, выбор ответственных, **enforcement анонимности — нет PII**), шифрование/дешифрование anon-ref, i18n-полнота ключей, фильтры ролей + object-level, валидаторы/типы контента, media-group агрегация, split 4096, CallbackData.

### Волна 1 — Каркас, конфиг, БД, миграции, crypto

Файлы: `pyproject.toml`, `.env.dist`, `bot/config.py`, `bot/db/{base,session,models,repositories}.py`, `bot/security/crypto.py`, `alembic/` + миграция `0001_initial`.
TDD: конфиг (.env, ADMIN_IDS, DSN, ANON_ENC_KEY), модели/репозитории (CRUD всех сущностей), `ticket_number` (формат, инкремент по type+year, сброс по годам, конкурентность на PG), атомарный захват (конкурентность на PG), crypto round-trip. Миграция: `upgrade`→`downgrade`→`upgrade` на чистой БД (обратимость с native_enum=False).
**→ Ревью волны 1.**

### Волна 2 — Фабрики, запуск, i18n, middleware

Файлы: `bot/factory.py`, `bot/runners/{polling,webhook}.py`, `bot/__main__.py`, `bot/locales/*/bot.ftl` (5), `bot/middlewares/{db,user,i18n_manager,throttling,media_group}.py`, `bot/security/logging.py`.
TDD: порядок middleware (одна сессия/апдейт, User до i18n), `SQLAlchemyManager`, i18n-полнота, выбор polling/webhook по конфигу, throttling (лимит срабатывает), media-group агрегация, PII-safe лог-фильтр, graceful shutdown (ресурсы закрыты).
**→ Ревью волны 2.**

### Волна 3 — Фильтры, клавиатуры, поток заявителя

Файлы: `bot/filters/roles.py`, `bot/keyboards/inline.py`, `bot/handlers/{start,language,submission,my_submissions}.py`, `bot/services/submissions.py`, `bot/utils/text.py`.
TDD: фильтры + object-level, CallbackData, валидаторы/типы контента, навигация FSM (назад с восстановлением при пропуске анонимных шагов; отмена с подтверждением), `create_submission` (commit-до-рассылки, выбор ответственных, **анонимность без PII и без author_user_id**, anon-ref шифруется, «нет ответственных→админам»), «Мои обращения» (анонимные скрыты), split 4096.
**→ Ревью волны 3.**

### Волна 4 — Поток ответственного и админа, ошибки, аудит

Файлы: `bot/handlers/{responsible,admin,errors}.py`, `bot/handlers/__init__.py`.
TDD: атомарный захват + **обновление кнопок у всех** по `SubmissionDelivery` (конкурентный тест на PG), ответ→пересылка заявителю **на его локали** (вкл. анонима через ref), закрытие (статус+closed_at+событие), `/assign`/`/responsibles` под IsAdmin, `AuditLog`/`SubmissionStatusEvent` пишутся, error-handler без PII, per-recipient обработка Forbidden/RetryAfter.
**→ Ревью волны 4.**

### Волна 5 — Инфраструктура и тотальное ревью

Файлы: `Dockerfile`, `docker-compose.yml` (+migrate one-shot, порт не наружу), `Makefile`, `README.md`, `set_my_commands` по локалям.
**→ Тотальное ревью** (параллельные агенты: баги, безопасность/приватность, aiogram-конвенции, i18n/UX) + e2e-прогон по чек-листу.

## Зависимости (pyproject.toml)
`aiogram>=3.27`, `aiogram-i18n`, `fluent.runtime`, `sqlalchemy[asyncio]>=2.0`, `asyncpg`, `alembic`, `redis`, `pydantic-settings`, `cryptography` (anon-ref), `aiohttp`. Dev: `pytest`, `pytest-asyncio`, `aiosqlite`, `testcontainers[postgres]` (конкурентные тесты), `ruff`.

## Тестирование / верификация

- **Unit/integration:** см. список выше; конкурентные и enforcement-тесты — приоритет.
- **i18n-полнота:** скрипт «все ключи во всех 5 локалях».
- **End-to-end (ручной, polling + тестовый токен):**
  1. `docker-compose up` (postgres+redis+migrate), `alembic upgrade head`.
  2. `/start` → язык → форма «Обращение граждан» с **альбомом из нескольких фото** → тикет + «Спасибо».
  3. Проверить «📋 Мои обращения» — обращение видно со статусом.
  4. `/assign` второму аккаунту (тип appeal) → карточка пришла на его языке.
  5. Два ответственных одновременно «Взять в работу» → у одного успех, у **всех** кнопки обновились на «В работе у …».
  6. «Ответить» → ответ пришёл заявителю **на его языке**; «Закрыть» → статус и событие в журнале.
  7. **Анонимная жалоба на коррупцию** → в карточке/БД нет ФИО/телефона/`author_user_id`; ответ доставлен через зашифрованный ref; в «Мои обращения» жалоба НЕ видна.
  8. Некорректный ввод (фото вместо текста, длинный >4096 текст) → подсказки/разбивка, без сброса.
  9. Смена языка в меню → весь UI переключился; внутри формы — подсказка завершить/отменить.
  10. Проверить `AuditLog`: назначение роли, смена статуса, ответ — записаны без PII.
- **Прод-режим:** `USE_WEBHOOK=true` + `WEBHOOK_URL`/`WEBHOOK_SECRET` → регистрация вебхука, приём апдейтов, секрет проверяется.

## Открытые вопросы (решить при реализации, не блокеры)
- Единообразие флагов в меню языков (🇺🇿 для трёх узбекистанских локалей либо без флагов).
- Точный сдержанный набор эмодзи — согласовать с заказчиком (гос-тон).
- Финальная вычитка каракалпакских формулировок носителем перед прод (помечать `# TODO: вычитка носителем`).
- Политика retention/удаления данных и шифрованных бэкапов БД (инфраструктурная, вне кода бота).

## Follow-ups из ревью Волны 1 (учесть в следующих волнах)
- **Enforcement анонимности (Волна 3):** `SubmissionRepository.create` сейчас принимает PII-поля без проверки взаимоисключения с `is_anonymous`. В сервисе `submissions` жёстко занулять `author_user_id/full_name/phone` при анонимной подаче + тест «нет PII».
- **Аудит без PII (Волна 4):** `AuditLog.meta/target` — свободный текст, гарантия «без PII» только в докстринге. Структурировать (whitelisted-поля), для анонима логировать только `public_id`/`submission_id`, никогда `tg_id`/контакты; тест на отсутствие `tg_id` в audit-payload.
- **Ротация ключа анонимности:** заложить `MultiFernet`/версионирование `ANON_ENC_KEY` до накопления боевых токенов.
- **Threat-model временно́й корреляции:** `users.created_at` vs `submissions.created_at` + Fernet-timestamp могут сужать круг анонима — задокументировать/решить (не создавать User в момент анонимной подачи либо принять риск).
- **Доставка анонимам:** ловить `InvalidToken`/`ValueError` при расшифровке, не светить детали пользователю; убедиться, что логгер не пишет `from_user.id` на анонимных хендлерах.
- **FK-индексы (опц.):** добавить индексы на `assigned_to_user_id`/`closed_by_user_id`, если появится запрос «обращения, назначенные мне».
- **Валидатор webhook:** при `use_webhook=True` требовать реальный `webhook_url`/`webhook_secret` (Волна 2). ✅ сделано.

## Follow-ups из ревью Волны 2 (учесть в следующих волнах)
- **Анонимность модели (важно, Волна 3):** `UserMiddleware` сохраняет `username`/`full_name` Telegram-профиля для ВСЕХ, включая тех, кто подаёт только анонимные жалобы. Дамп `users` + корреляция по `created_at` ослабляет анонимность. Решить: не сохранять профиль для анонимных-only, либо явно задокументировать остаточный риск + разорвать временну́ю корреляцию.
- **Карточка анонима (Волна 3-4):** гарантировать, что `card-from`/`card-phone`/профиль НЕ рендерятся для анонимных; только `card-anonymous`.
- **Честный дисклеймер (Волна 3):** в тексте про анонимность добавить «не указывайте свои данные в тексте обращения» и честно обозначить, что для доставки ответа хранится зашифрованная ссылка.
- **Аудит повышения до admin (Волна 4):** `UserMiddleware` повышает `is_admin` без записи в `AuditLog`; права не снимаются при удалении из `ADMIN_IDS`. Логировать повышение; продумать разжалование.
- **Webhook при нескольких репликах:** `set_webhook(drop_pending_updates=True)` в `on_startup` ломается при N репликах — в проде миграции/`set_webhook` выносить в one-shot job (в плане инфра-Волны 5 это уже заложено как отдельный job).
- **Per-IP throttle:** per-user недостаточно против мульти-аккаунт флуда — рассмотреть rate-limit на reverse-proxy (инфраструктура).
- **Лимиты вложений (Волна 3):** ограничить число/размер вложений в форме (middleware-троттлинг их не лимитирует).

## Follow-ups из ревью Волны 3 (учесть в следующих волнах)
- **Escape в Волне 4:** хендлеры реакции рендерят `card-assigned`/`cb-already-taken` с `{ $name }` ответственного — экранировать `escape(name)` (HTML parse mode), иначе инъекция разметки.
- **Остаточный риск анонимности (задокументирован):** при компрометации `ANON_ENC_KEY` оператор может деанонимизировать (key→tg_id→users-строка); плюс корреляция по `created_at`. Комментарий в `services/submissions.py` исправлен (не переобещать). Долгосрочно: не хранить профиль для анонимных-only, ротация ключа (MultiFernet).
- **EXIF/метаданные вложений:** предупреждение усилено в тексте; стрипинг EXIF не делаем (принятый компромисс), отметить в README.
- **Дисклеймер «не указывайте себя в тексте»:** добавлен (`form-text-anon-hint`) на шаге текста для анонимных. ✅

## Изменение режима деплоя (после Волны 5): один контейнер на веб-панели

Целевой прод — **старая хостинг-панель без docker-compose**, которая умеет запускать **один контейнер** с Python-приложением; PostgreSQL и Redis уже предоставлены панелью. Решения:

- **Авто-миграция при старте.** Бот применяет `alembic upgrade head` программно в `__main__.main()` до построения фабрики, под флагом `RUN_MIGRATIONS_ON_STARTUP` (default `true`). Реализация — `bot/db/migrate.py` (`upgrade_to_head` синхронно через `command.upgrade`; `run_upgrade_to_head` запускает его в отдельном потоке `asyncio.to_thread`, т.к. Alembic env держит собственный event loop). На старте — ретраи ожидания готовности БД (default 10×1s).
- **`alembic/env.py`** теперь уважает `sqlalchemy.url` из переданного Config (override помимо Settings) и поддерживает sync- и async-DSN (sync-ветка нужна для тестов на sqlite-файле).
- **Убран one-shot `migrate`-сервис** из `docker-compose.yml` (он оставлял за собой `Exited (0)`-контейнер → конфликт имён при повторном `up`). Compose теперь — только для локальной разработки; прод — один контейнер `python -m bot`.
- **Мульти-реплика:** при горизонтальном масштабировании выставить `RUN_MIGRATIONS_ON_STARTUP=false` и катать миграции отдельным шагом (реплики не должны гоняться за апгрейд). Задокументировано в README.
- Тесты: `tests/test_migrate.py` (создание схемы на sqlite-файле, идемпотентность, off-thread async-обёртка, ретраи и исчерпание ретраев); флаг покрыт в `tests/test_config.py`. `base_env` сделан герметичным (явный пустой `REDIS__PASSWORD`), чтобы не подхватывать реальный `.env`.

## Follow-ups из ревью Волны 4
- **Глобальный error-handler:** был на листовом под-роутере (не срабатывал) → перенесён на root через `errors.register_errors(root)`. ✅ + регресс-тест.
- **Throttling FSM-exempt:** `state` недоступен на outer-уровне → троттлинг перенесён на `message`/`callback_query` (inner, после FSMContextMiddleware). ✅
- **`update_all_cards`:** теперь ловит `TelegramForbiddenError`/`TelegramRetryAfter` (не только BadRequest). ✅
- **Аудит статуса:** `close()` возвращает прежний статус из атомарного UPDATE (точный `from_status`). ✅
- **Ownership-guard:** `reply`/`close` разрешены только назначенному (или admin) — модель first-claim. ✅ + регресс-тест.
- **Остаточные (записать в README):** двусторонняя анонимность подтверждена (имя ответственного не уходит заявителю); компрометация `ANON_ENC_KEY` → деанон (документировано); reply к закрытому возможен (не приватность, для будущей доработки state-machine); PII-фильтр логов не покрывает rendered traceback (backstop).
- **Негативные authz-тесты:** добавлены (не-владелец не может закрыть). Не-админ при `/assign` — фильтр есть; полноценный негативный тест можно добавить в Волне 5.
