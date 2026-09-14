# Matrix (Element): личные чаты вместо общих комнат — дизайн

**Дата:** 2026-09-14 · **Проект:** anticor-bot · **Статус:** утверждён

Развивает [2026-09-11-matrix-bridge-design.md](2026-09-11-matrix-bridge-design.md).

## Проблема

Мост доставляет карточки в **общую комнату на тип заявки**, а роль сотрудника
выводится из членства в комнате. На проде обе переменные `MATRIX__ROOM_*`
указывают на одну комнату — из-за этого `type_for_room()` всегда отвечает
`appeal`, участник получает только `resp_appeal`, и взять/ответить на жалобу
из Element невозможно (`authorize()` отказывает). Заказчик хочет, чтобы Element
работал **как Telegram**: каждая заявка приходит каждому ответственному в его
личный чат; ответственных назначает администратор; кто первым взял — того
заявка; действия из любого канала синхронно обновляют карточки в обоих.

Попутно: после `alembic upgrade head` при старте бот перестаёт логировать —
`fileConfig(alembic.ini)` глушит существующие логгеры (`disable_existing_loggers`)
и снимает `PiiRedactingFilter`. На проде это скрыло бы любую ошибку моста.

## Ключевой принцип (без изменений)

Matrix — точка входа к существующим правам. Каждое действие выполняется от
имени своего `User`; авторизация — `SubmissionActions.authorize()` по типу из
строки заявки. Меняется только **откуда берутся роль и адрес доставки**:

| | Было (комната) | Стало (DM) |
|---|---|---|
| Кто получает карточку | участники комнаты типа | `users` с `matrix_id` и `resp_<type>` |
| Кто даёт роль | членство в комнате | администратор, `/assign @user:server` |
| Куда идёт карточка | `MATRIX__ROOM_<type>` | `users.matrix_room_id` (DM с ботом) |
| Кто может действовать в комнате | любой участник | только владелец DM (`sender == matrix_id`) |
| Авторизация действия | `authorize()` | **та же** |
| Синхронизация карточек | одна карточка в комнате | все DM-карточки заявки + Telegram |

## Объём

### Входит

1. **Назначение по Matrix ID.** `/assign @user:server` (кроме forward и tg_id)
   создаёт/находит `User` по `matrix_id` и предлагает тип; `/responsibles`
   уже показывает Matrix-пользователей и снимает роли — без изменений.
   Автоматическое присвоение роли по комнате (`get_or_create_matrix`) удаляется.
2. **DM-комната на сотрудника** — `users.matrix_room_id`. Привязка двумя путями:
   - сотрудник сам пишет боту в Element → бот принимает приглашение (как сейчас),
     и если комната 1:1, привязывает её к `User` по `matrix_id` (создавая
     запись без ролей, если её нет) и отвечает `mx-dm-welcome` с его ID —
     администратор берёт ID отсюда;
   - при первой доставке сотруднику без комнаты бот создаёт DM сам
     (`room_create(is_direct=True, invite=[matrix_id])`).
3. **Рассылка как в Telegram.** `announce()` — каждому ответственному типа
   с `matrix_id`: вложения + карточка в его DM; по строке
   `matrix_deliveries` на событие. `refresh()` правит **все** карточки заявки
   (не только последнюю). Резолв reply → заявка по `(room_id, event_id)` —
   без изменений.
4. **Входящие.** Комната события → `User` по `matrix_room_id`; `sender`
   должен совпадать с `matrix_id`, иначе событие игнорируется. Дальше —
   существующие `!olish / !yopish / !karta / !yordam` и текстовый ответ через
   `SubmissionActions`. Ответ из Element уходит заявителю в Telegram
   (`actions.reply` — уже так).
5. **Конфигурация.** `MATRIX__ROOM_APPEAL / ROOM_CORRUPTION` удаляются;
   мост включён при `homeserver + user + (password | token)`.
6. **Логирование при миграции.** Программный запуск (`migrate.py`) помечает
   `config.attributes["configure_logging"] = False`; `env.py` тогда не зовёт
   `fileConfig`. CLI `alembic upgrade head` — как раньше.
7. **Мелочи в admin.** `_refresh_commands` не вызывается для `tg_id IS NULL`;
   `admin-assigned` показывает `matrix_id`, если нет `tg_id`;
   `admin-assign-usage` описывает третий вариант.

### Не входит

Маршрутизация одной заявки одному человеку · локаль на Matrix-пользователя
(остаётся `MATRIX__LOCALE`) · сохранение режима общих комнат · эхо текста
ответа в другой канал (паритет с Telegram) · E2EE.

## Архитектура

### Данные

`users.matrix_room_id VARCHAR(255) NULL UNIQUE` — миграция `0003`. Одна
комната на пользователя; комната принадлежит ровно одному пользователю.
`matrix_deliveries` не меняется: `(room_id, event_id)` уникальны, у одной
заявки теперь несколько строк `kind='card'` — по числу получателей.

`UserRepository`:
- `get_or_create_by_matrix_id(matrix_id, full_name=None)` — без ролей
  (заменяет `get_or_create_matrix`);
- `by_matrix_room(room_id)`; `matrix_responsibles_for(type_)` —
  `matrix_id IS NOT NULL AND resp_<type>`.

`MatrixDeliveryRepository.cards_for(submission_id)` — все карточки.

### `bot/matrix/client.py`

- `_on_sync`: после `join` — если в комнате ровно два участника (бот и один
  человек), вызвать `self._dm_handler(room_id, other_user_id)`; иначе, как
  раньше, написать `Room id`. Бридж регистрирует обработчик через
  `on_direct_room(handler)`.
- `create_dm(user_id) -> str` — `room_create(is_direct=True, invite=[...],
  preset=trusted_private_chat)`; `''` при ошибке.
- `room_members(room_id) -> list[str]` — для проверки 1:1.

### `bot/matrix/bridge.py`

- `announce`: получатели — `matrix_responsibles_for(type)`; для каждого —
  `_ensure_room(user)` (существующая или `create_dm` + запись в `User`),
  затем вложения + карточка. `True`, если доставлено хотя бы одному.
- `refresh`: `cards_for` → `edit_html` по каждой.
- `handle_event`: `by_matrix_room(room_id)`; `None` или
  `sender != user.matrix_id` → return. Роль не присваивается.
- `on_direct_room(room_id, mxid)`: `get_or_create_by_matrix_id` +
  `matrix_room_id = room_id` (если ещё не привязан) + `mx-dm-welcome`.
  Собственная сессия, как у `handle_event`.
- `_repost`: в комнату отправителя (`user.matrix_room_id`).

### `bot/handlers/admin.py`

`_target(message) -> tuple[str, int | str] | None`: `("tg", id)` из forward или
цифр, `("matrix", "@x:y")` из аргумента вида `@localpart:domain`. Для Matrix —
`get_or_create_by_matrix_id`. Остальной поток (клавиатура типа, аудит) общий.

### Локализация

Во всех пяти `.ftl`: обновить `admin-assign-usage`; добавить `mx-dm-welcome`
(«Ваш Matrix ID: `{ $id }`. Администратор должен назначить вам роль…»).

### Обработка ошибок

- `create_dm` не удался → получатель пропущен, `announce` продолжает по
  остальным (как `safe_send` в Telegram); лог WARNING без PII.
- Сотрудник не принял приглашение → карточки копятся в комнате, прочитает
  при входе. Ничего не делаем.
- Событие из старой общей комнаты → нет привязки → игнор. Оператор может
  удалить бота из комнаты.

## Поток данных

```
Заявка создана (Telegram)
  → SubmissionService.notify_responsibles  → Telegram-карточки resp_<type> с tg_id
  → card_sinks.announce                    → DM-карточки resp_<type> с matrix_id

!olish в DM (Element)
  → by_matrix_room → User → authorize(type из заявки) → try_claim
  → update_all_cards (Telegram) + refresh (все DM) 

Текст в DM (reply на карточку)
  → authorize(require_owner) → reply → safe_send заявителю (Telegram)

Взять/закрыть в Telegram
  → handlers/responsible → SubmissionActions → refresh → все DM-карточки
```

## Тесты (только фейки, без сети)

`tests/test_matrix_bridge.py` переписывается под DM:
- рассылка двум ответственным типа — две карточки, две строки deliveries,
  вложения в каждую комнату; ответственный другого типа — не получает;
- сотрудник без комнаты → `create_dm` вызван, `matrix_room_id` сохранён;
  отказ `create_dm` не ломает доставку остальным;
- `refresh` правит **все** карточки заявки;
- `!olish` из DM: победитель один, вторая DM-карточка перерисована
  (`assignee`), Telegram-карточки обновлены (`bot.edit_message_text`);
- текстовый ответ из DM → `SubmissionResponse` + `bot.send_message`
  заявителю; для анонимной заявки — через `enc_chat_ref`;
- чужой `sender` в чужой DM, неизвестная комната, событие до `started_ms`,
  свои события — игнор;
- пользователь с `resp_appeal` отвечает на жалобу → `mx-forbidden`,
  роль не дописывается;
- закрытие из Telegram (через `SubmissionActions` с sink) → DM-карточки
  перерисованы.

Новые: `test_matrix_users.py` — `get_or_create_by_matrix_id` без ролей,
`by_matrix_room`, `matrix_responsibles_for`; `test_responsible_admin.py` —
`/assign @x:y` → выбор типа → `resp_*`, `admin-assigned` с matrix_id,
`_refresh_commands` не вызван; `test_matrix_client.py` — `_on_sync` зовёт
`dm_handler` для 1:1 и `Room id` для групп; `test_migrate.py` — при
программном запуске `fileConfig` не вызывается, логгер `bot` остаётся
включённым и на уровне INFO; `test_config.py` — `enabled` без комнат;
`test_i18n.py` — паритет ключей (уже есть).

Запуск — в Docker `python:3.12-slim` (локальный Python 3.10).

## Развёртывание

1. Миграция `0003` применится при старте.
2. Убрать `MATRIX__ROOM_*` из `.env`.
3. `@uge132:gidro.uz` пишет боту в Element → получает `mx-dm-welcome`;
   администратор: `/assign @uge132:gidro.uz` → тип.
4. Тестовая заявка из Telegram → карточка в DM; `!olish`, текст, `!yopish`.
5. Бота можно удалить из старой общей комнаты.
