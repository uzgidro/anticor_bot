# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Telegram bot for AO "Uzbekgidroenergo": citizen appeals (`appeal`) and anti-corruption complaints (`corruption`), the latter optionally **anonymous**. 5 locales, role-routed to responsible persons, statuses + replies back to the applicant, full audit trail.

Stack: Python 3.11+, aiogram 3.x, aiogram-i18n + Fluent, SQLAlchemy 2.0 async (asyncpg) + Alembic, Redis (FSM + throttling). Optional Matrix (Element) bridge via matrix-nio.

User-facing text (README, locales, plan docs) is Russian; code and comments are English. Keep that split.

## Commands

```bash
make install        # python -m venv .venv && pip install -e ".[dev]"
make lint           # ruff check bot tests
make test           # pytest -q
make run            # python -m bot  (migrations auto-apply on startup)
make migration m="add X"   # alembic revision --autogenerate
make migrate        # alembic upgrade head
make up / down / logs      # docker compose (LOCAL DEV ONLY — prod is a single container)
```

Single test / single case:

```bash
pytest tests/test_repositories.py -q
pytest tests/test_handlers_flow.py::test_anonymous_corruption_flow_persists_no_pii -q
```

`tests/test_concurrency.py` needs **real Postgres** — it is skipped otherwise. Provide it via `TEST_PG_DSN` (preferred, and what CI does); testcontainers is the fallback but is unreliable under MSYS on Windows:

```bash
TEST_PG_DSN=postgresql+asyncpg://anticor:anticor@localhost:5432/anticor_test pytest -q
```

pytest runs in `asyncio_mode = "auto"` — async tests need no `@pytest.mark.asyncio`. Ruff: line-length 100, rules `E,F,I,UP,B,ASYNC`.

## Architecture

### Composition root

[bot/factory.py](bot/factory.py) `build()` is the only place Bot/Dispatcher/Redis/engine/i18n are wired; [bot/\_\_main\_\_.py](bot/__main__.py) runs migrations, then polling or webhook per `USE_WEBHOOK`. Handlers never construct these — they receive `session`, `db_user`, `i18n`, `settings` from middleware / dispatcher context.

### Middleware order is load-bearing

Registered in [bot/factory.py:74-86](bot/factory.py#L74-L86):

1. `DbSessionMiddleware` (outer) — one `AsyncSession` per update, commit on success / rollback on exception.
2. `UserMiddleware` (outer) — get-or-create ORM `User` → `data["db_user"]`; bootstraps admins from `ADMIN_IDS`.
3. `I18nMiddleware` (outer) — `DBLocaleManager` reads `db_user.language`, so the user must already exist.
4. `ThrottlingMiddleware` — registered as **inner** middleware on `message`/`callback_query`, not outer, because it must run *after* aiogram's `FSMContextMiddleware` to read `state` and exempt users mid-form. (The docstring in [throttling.py](bot/middlewares/throttling.py) still says "FIRST outer middleware" — the factory is the source of truth.)
5. `MediaGroupMiddleware` — messages only.

Changing this order breaks locale resolution or drops legitimate multi-step form input.

### Anonymity is the security-critical invariant

For `is_anonymous` submissions, [SubmissionService.create](bot/services/submissions.py#L91) hard-nulls `author_user_id` / `full_name` / `phone` at the service layer regardless of what the FSM collected. The author's tg_id survives **only** as a Fernet token in `anon_delivery_refs.enc_chat_ref`, with `submission_id` baked into the plaintext as poor-man's AAD ([crypto.py](bot/security/crypto.py)) so a token can't be moved between rows.

Scope of the guarantee — state it precisely, never overstate it:
- Protects against a **DB dump alone** (no plaintext author).
- Does **not** protect against an operator holding `ANON_ENC_KEY`; a cleartext `users` row with that tg_id exists (created by `UserMiddleware`).
- Residual timing correlation: `users.created_at` vs `submissions.created_at`.

Also: `create_engine(..., echo=False)` is deliberate — SQL echo would leak submission text/names/phones into logs. Audit `meta` must never carry PII. "My submissions" excludes anonymous ones.

### Atomicity

Both live in [bot/db/repositories.py](bot/db/repositories.py) and are proven only by the Postgres concurrency tests:

- `next_ticket_number` — `INSERT … ON CONFLICT DO UPDATE … RETURNING` on `ticket_counters` (per type+year). Has a Postgres branch and a SQLite branch for unit tests.
- `try_claim` — conditional `UPDATE … WHERE status='new' RETURNING id`; exactly one responsible wins, no TOCTOU. Because it bypasses the ORM flush, `updated_at` is bumped explicitly (`onupdate` does not fire on a Core UPDATE).
- `close` — reads the prior status inside the same transaction before the conditional UPDATE, so the audit event is accurate under concurrency.

Ordering rule in [handlers/submission.py](bot/handlers/submission.py#L280): **commit before any network fan-out** — a responsible must never receive a card for a submission a later rollback would erase. FSM state is cleared *before* the work so a double-tap on Submit hits the `"type" not in data` guard.

### Authorization

`IsAdmin` / `IsResponsible` in [bot/filters/roles.py](bot/filters/roles.py) gate routers by role only. Object-level authz (may this user act on *this* submission) is re-checked server-side in [responsible.py `_authorize`](bot/handlers/responsible.py#L32) — callback data is client-supplied and never trusted for authz. `require_owner=True` (reply/close) additionally demands the assignee-or-admin, since ownership is first-claim.

### Registry ([bot/handlers/registry.py](bot/handlers/registry.py))

A browsable list for responsibles/admins — a new **entry point to existing rights**, never new rights. Two invariants hold it together:

- Visibility is `can_handle_type()`, the same check that gates the push card, re-checked on **every** callback because a keyboard outlives a role change and callback data is client-supplied. In `_show_detail` the type is re-derived from the row, so a crafted `public_id` cannot smuggle a foreign-type submission past the gate.
- Actions are **not** reimplemented: the detail keyboard emits the existing `ReactionCb`, handled by `responsible.py` with its `_authorize()`. `registry.refresh_detail()` only redraws afterwards. Never add a competing `ReactionCb` handler — that splits authz in two.

Filter/sort/page state lives in `RegistryCb` (43 bytes worst case, Telegram's cap is 64) rather than FSM, so it never collides with `SubmissionForm` and survives a restart. `registry.py` must not import `responsible.py` — the dependency is one-way, enforced by a test.

`set_personal_commands()` uses `BotCommandScopeChat`, which **replaces** the whole list for that chat: it re-sends the base commands with the registry ones, and deletes the scope when the last role is revoked.

### Matrix bridge ([bot/matrix/](bot/matrix/))

A second channel for responsibles, on the same rights. Take/close/reply live
**once** in [services/actions.py](bot/services/actions.py) (`SubmissionActions`);
`handlers/responsible.py` and `matrix/bridge.py` are adapters over it. Never
put a second copy of those sequences anywhere.

- `users.tg_id` is **nullable**: a room member is provisioned as a `User` with
  `matrix_id` on first action (`UserRepository.get_or_create_matrix`), role =
  the room's type. `responsibles_for()` excludes `tg_id IS NULL` — never send
  Telegram messages to Matrix-only users. `CHECK ck_users_identity` requires
  one of the two ids.
- Authorization is still `SubmissionActions.authorize()` with the type taken
  from the submission row — a card addressed from the wrong room grants
  nothing. `authorize()` re-reads the row because `try_claim`/`close` are Core
  UPDATEs that bypass the identity map.
- Reply resolution is room-scoped (`matrix_deliveries(room_id, event_id)`).
- Cross-channel redraw goes through the `CardSink` protocol; handlers get
  `card_sinks` from dispatcher context and never import `bot.matrix`
  ([tests/test_matrix_architecture.py](tests/test_matrix_architecture.py)).
- Matrix events run outside the middleware chain: the bridge opens its own
  session per event (commit/rollback like `DbSessionMiddleware`).
- Rooms are unencrypted (no `[e2e]` extra, no libolm). Room strings are
  `mx-*` keys in all five `.ftl` files, rendered in `MATRIX__LOCALE`.
- Local Python here may be older than 3.11; run the suite in Docker
  (`python:3.12-slim`, `pip install -e .[dev]`, then `ruff check bot tests && pytest -q`).

### Multi-locale messaging

Two distinct paths, don't mix them:
- **To the current user** — `i18n.get(key, **kw)` via `I18nContext`.
- **To another user** (card to a responsible, reply to an applicant) — `core.get(key, <their_locale>, **kw)` with the recipient's locale read from `User.language`. `_LocaleShim` in [submissions.py](bot/services/submissions.py#L59) adapts a `BaseCore` to the `i18n.get()` interface that keyboard builders expect.

All 5 `.ftl` files must have **identical key sets** — [tests/test_i18n.py](tests/test_i18n.py) enforces parity and that specific keys resolve distinctly (a past bug had Fluent *attributes* accessed via the wrong API, silently returning the message body). Add a key → add it to all five: `ru`, `kaa`, `uz_cyrl`, `uz_latn`, `en`.

Telegram's `set_my_commands` accepts only ISO 639-1, so [runners/commands.py](bot/runners/commands.py) maps internal locales → `_TG_CODE` (`uz_cyrl`/`uz_latn`/`kaa` all collapse to `uz`; first wins).

### Telegram-specific handling

- User text always goes through `escape()` before HTML parse mode, and `split_text()` for the 4096 limit ([utils/text.py](bot/utils/text.py)). On split, only the final chunk carries the keyboard.
- Fan-out uses `safe_send` / `_send_with_retry`: swallow `TelegramForbiddenError` (bot blocked), one retry on `TelegramRetryAfter` — one bad recipient must never break the rest.
- Albums arrive as N messages sharing `media_group_id`; `MediaGroupMiddleware` debounces (0.6s) and calls the handler **once** with `album: list[Message]`.
- The phone step uses a `ReplyKeyboardMarkup` (Telegram allows `request_contact` only there), then `ReplyKeyboardRemove` before the next inline step.
- Redis FSM has a 6h `state_ttl`/`data_ttl` — abandoned drafts hold PII and must not live forever.

## Migrations & deploy

`RUN_MIGRATIONS_ON_STARTUP=true` (default): [db/migrate.py](bot/db/migrate.py) runs `alembic upgrade head` in-process, offloaded via `asyncio.to_thread` because Alembic's `env.py` owns its own event loop. Retries 10× for a warming-up DB. **Set it to `false` if you ever run multiple replicas** — they would race the upgrade.

Prod target is a **single container** on a hosting panel with panel-provided Postgres/Redis; `docker-compose.yml` is dev-only. Webhook mode binds `127.0.0.1` — TLS terminates at the panel's reverse proxy. Push to `main` → CI (ruff + pytest with service Postgres) → Docker Hub → Trivy → Watchtower.

Config is pydantic-settings with `__` nesting (`POSTGRES__HOST`); secrets are `SecretStr`. `ANON_ENC_KEY` must be a valid Fernet key or the bot refuses to start.

### Image hygiene — `.dockerignore` is load-bearing

The Dockerfile copies only `pyproject.toml`, `alembic.ini`, `bot/`, and `alembic/`. Never revert it to `COPY . .`: with the build context unfiltered, that baked **`.env` — the real `BOT_TOKEN` and `ANON_ENC_KEY` — into a published Docker Hub image**, and copied the local `.venv`, whose stale `setuptools` failed the Trivy gate.

`pip`/`setuptools`/`wheel` are uninstalled after `pip install .`: the container only runs `python -m bot`, and pip's *vendored* copies (`pip/_vendor/msgpack`) are scanner findings that cannot be patched any other way. If you add a runtime that shells out to pip, this breaks.

Verify a build locally before trusting CI:

```bash
docker build --pull -t anticor:test .
docker run --rm anticor:test sh -c '[ -f /app/.env ] && echo LEAK || echo clean'
docker run --rm -v /var/run/docker.sock:/var/run/docker.sock aquasec/trivy:0.69.3 \
  image --scanners vuln --severity HIGH,CRITICAL --ignore-unfixed --exit-code 1 anticor:test
```

## Knowledge base

Per global instructions, project findings/cases go to the Obsidian vault at `E:\projects\obsidian\sukhrob` — see `Проекты/python/` and `Кейсы/`, tag `#проект/anticor-bot`.
