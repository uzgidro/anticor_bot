# Matrix (Element) Bridge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver submission cards into two Element rooms (one per submission type) and let room members take, reply to and close submissions from there — as individual `User`s, through the same service operations and authorization the Telegram handlers use.

**Architecture:** The take/reply/close sequences move out of `handlers/responsible.py` into a new `bot/services/actions.py` (`SubmissionActions`); Telegram handlers become thin adapters over it. A new `bot/matrix/` package (nio client wrapper, renderer, bridge) turns room events into `SubmissionActions` calls, provisioning a `User` with `matrix_id` on a member's first action. Cross-channel card refresh goes through a `CardSink` protocol the handlers receive from dispatcher context, so `bot.handlers` never imports `bot.matrix`.

**Tech Stack:** Python 3.11+, aiogram 3.x, aiogram-i18n + Fluent, SQLAlchemy 2.0 async + Alembic, matrix-nio (no `[e2e]`), pytest (asyncio_mode=auto), ruff.

**Spec:** [docs/superpowers/specs/2026-09-11-matrix-bridge-design.md](../specs/2026-09-11-matrix-bridge-design.md)

## Global Constraints

- **No new rights.** Every Matrix action runs as a `User` row through `can_handle_type()` and the owner check. Room membership only decides *which* `User` and which `resp_<type>` flag gets provisioned.
- **Actions are not reimplemented.** Take/close/reply exist once, in `SubmissionActions`. `responsible.py` and `bot/matrix/bridge.py` both call it. Never add a second copy.
- **Anonymity untouched.** The bridge renders through `render_card()` and delivers through `resolve_author_chat_id()`. It never touches `anon_delivery_refs`. Audit `meta` never carries PII (no Matrix IDs, no names).
- **Commit before fan-out.** Status/audit rows are committed before any network send, exactly as today.
- **Import boundary:** `bot/matrix/*` must not import `bot.handlers`; `bot/handlers/*` must not import `bot.matrix`. Enforced by `tests/test_matrix_architecture.py`.
- **`users.tg_id` becomes nullable.** `responsibles_for()` must exclude `tg_id IS NULL` so nobody tries to send Telegram messages to Matrix-only users. Admin listings show `matrix_id` when `tg_id` is None.
- **Migrations must run on SQLite too** (`tests/test_migrate.py` uses a file SQLite DB) → use `op.batch_alter_table` for the `users` changes.
- **i18n parity is enforced by CI.** Every new key goes into all 5 locales: `ru`, `kaa`, `uz_cyrl`, `uz_latn`, `en`. Room locale default is `uz_latn`.
- **All user text through `escape()`** before it enters HTML (both Telegram and Matrix `formatted_body`).
- **Ruff:** line-length 100, rules `E,F,I,UP,B,ASYNC`. Run before every commit.
- **Test runner (this machine has Python 3.10; the project needs 3.11+):** run everything in Docker. Baseline before this work: **134 passed, 3 errors** (the 3 errors are `test_concurrency.py` needing Postgres; they are expected without `TEST_PG_DSN`).

  ```bash
  cd /c/Users/uge226/Desktop/anticor_bot
  MSYS_NO_PATHCONV=1 docker run --rm \
    -v "$(pwd -W):/app" \
    -v "C:/php/extras/ssl/cacert.pem:/certs/cacert.pem:ro" \
    -e PIP_CERT=/certs/cacert.pem -e SSL_CERT_FILE=/certs/cacert.pem \
    -w /app python:3.12-slim \
    sh -c "pip install -q -e '.[dev]' 2>/dev/null; ruff check bot tests && pytest -q"
  ```

  The pip install repeats on every run (~40 s). Acceptable; do not add a Dockerfile change for it.
- **Branch:** all work on `feature/matrix-bridge`. **Never push to `main`** — CI deploys `main` to production via Watchtower.
- **Language:** code, comments, commit messages — English. Locale strings — in their locale. README section — Russian.

---

### Task 1: Dependency and `MatrixSettings`

**Files:**
- Modify: `pyproject.toml` (dependencies list)
- Modify: `bot/config.py` (add `MatrixSettings`, `Settings.matrix`)
- Modify: `.env.dist` (append a Matrix block)
- Test: `tests/test_config.py` (append tests)

**Interfaces:**
- Produces: `MatrixSettings` with fields `homeserver: str`, `user: str`, `password: SecretStr`, `token: SecretStr`, `room_appeal: str`, `room_corruption: str`, `locale: str = "uz_latn"`, `store_dir: str = "matrix_store"`, `device_name: str = "anticor-bot"`; property `enabled -> bool`; `room_for(type_value: str) -> str`; `type_for_room(room_id: str) -> str | None`. `Settings.matrix: MatrixSettings`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_config.py`:

```python
def test_matrix_disabled_by_default():
    s = Settings(_env_file=None, bot_token="1:x", anon_enc_key=Fernet.generate_key().decode())
    assert s.matrix.enabled is False
    assert s.matrix.locale == "uz_latn"


def test_matrix_enabled_with_password_or_token(monkeypatch):
    monkeypatch.setenv("MATRIX__HOMESERVER", "https://matrix.example.uz")
    monkeypatch.setenv("MATRIX__USER", "@bot:example.uz")
    monkeypatch.setenv("MATRIX__PASSWORD", "pw")
    s = Settings(_env_file=None, bot_token="1:x", anon_enc_key=Fernet.generate_key().decode())
    assert s.matrix.enabled is True
    # Secrets never leak through repr.
    assert "pw" not in repr(s.matrix)

    monkeypatch.delenv("MATRIX__PASSWORD")
    monkeypatch.setenv("MATRIX__TOKEN", "syt_abc")
    s = Settings(_env_file=None, bot_token="1:x", anon_enc_key=Fernet.generate_key().decode())
    assert s.matrix.enabled is True


def test_matrix_room_mapping(monkeypatch):
    monkeypatch.setenv("MATRIX__ROOM_APPEAL", "!a:example.uz")
    monkeypatch.setenv("MATRIX__ROOM_CORRUPTION", "!c:example.uz")
    s = Settings(_env_file=None, bot_token="1:x", anon_enc_key=Fernet.generate_key().decode())
    assert s.matrix.room_for("appeal") == "!a:example.uz"
    assert s.matrix.room_for("corruption") == "!c:example.uz"
    assert s.matrix.type_for_room("!a:example.uz") == "appeal"
    assert s.matrix.type_for_room("!c:example.uz") == "corruption"
    assert s.matrix.type_for_room("!other:example.uz") is None
```

Make sure `from cryptography.fernet import Fernet` and `from bot.config import Settings` are imported at the top of `tests/test_config.py` (they already are if the file tests `Settings`; add if missing).

- [ ] **Step 2: Run tests to verify they fail**

Run the Docker test command with `pytest -q tests/test_config.py`.
Expected: FAIL — `AttributeError: 'Settings' object has no attribute 'matrix'`.

- [ ] **Step 3: Add the dependency**

In `pyproject.toml`, in `dependencies = [...]`, after `"cryptography>=42.0",` add:

```toml
    "matrix-nio>=0.25",
```

- [ ] **Step 4: Add `MatrixSettings`**

In `bot/config.py`, after `class RedisSettings`, add:

```python
class MatrixSettings(BaseSettings):
    """Matrix (Element) bridge. Empty homeserver/user = bridge disabled.

    Rooms are per submission type; membership of a room is what provisions a
    User with the matching resp_<type> flag (see services/actions + matrix/bridge).
    """

    homeserver: str = ""
    user: str = ""
    password: SecretStr = SecretStr("")
    token: SecretStr = SecretStr("")
    room_appeal: str = ""
    room_corruption: str = ""
    locale: str = "uz_latn"
    store_dir: str = "matrix_store"
    device_name: str = "anticor-bot"

    @property
    def enabled(self) -> bool:
        has_secret = bool(self.password.get_secret_value() or self.token.get_secret_value())
        return bool(self.homeserver and self.user and has_secret)

    def room_for(self, type_value: str) -> str:
        """Room id for a SubmissionType value ('appeal' | 'corruption'), '' if unset."""
        return self.room_appeal if type_value == "appeal" else self.room_corruption

    def type_for_room(self, room_id: str) -> str | None:
        """Inverse of room_for: which submission type a room serves, or None."""
        if room_id and room_id == self.room_appeal:
            return "appeal"
        if room_id and room_id == self.room_corruption:
            return "corruption"
        return None
```

In `class Settings`, after `redis: RedisSettings = Field(default_factory=RedisSettings)` add:

```python
    matrix: MatrixSettings = Field(default_factory=MatrixSettings)
```

- [ ] **Step 5: Document in `.env.dist`**

Append to `.env.dist`:

```dotenv

# ===== Matrix (Element) — optional =====
# Leave HOMESERVER/USER empty to run without the bridge.
# Rooms must NOT be end-to-end encrypted. Being a member of a room grants the
# matching resp_<type> role on first action — control room membership strictly.
MATRIX__HOMESERVER=
MATRIX__USER=@anticorbot:example.uz
MATRIX__PASSWORD=
# Alternatively an access token instead of a password:
MATRIX__TOKEN=
MATRIX__ROOM_APPEAL=
MATRIX__ROOM_CORRUPTION=
# Locale for room messages: ru | kaa | uz_cyrl | uz_latn | en
MATRIX__LOCALE=uz_latn
# nio sync state; mount as a volume in the container
MATRIX__STORE_DIR=matrix_store
```

- [ ] **Step 6: Run tests to verify they pass**

Run the Docker test command with `pytest -q tests/test_config.py`.
Expected: PASS (all, including the three new tests).

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml bot/config.py .env.dist tests/test_config.py
git commit -m "Add MatrixSettings and the matrix-nio dependency"
```

---

### Task 2: Data model and migration

**Files:**
- Modify: `bot/db/models.py` (`User.tg_id` nullable, `User.matrix_id`, CHECK; new `MatrixDelivery`)
- Create: `alembic/versions/0002_matrix.py`
- Test: `tests/test_migrate.py` (append), `tests/test_repositories.py` (append)

**Interfaces:**
- Produces: `User.tg_id: int | None`, `User.matrix_id: str | None`; `MatrixDelivery(submission_id, room_id, event_id, kind, created_at)` with `kind in {"card", "attachment", "note"}`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_migrate.py`:

```python
def test_upgrade_adds_matrix_schema(tmp_path):
    """Revision 0002: users.matrix_id, nullable tg_id, matrix_deliveries."""
    url, db_path = _sqlite_url(tmp_path)
    upgrade_to_head(url)

    con = sqlite3.connect(db_path)
    try:
        cols = {row[1]: row for row in con.execute("PRAGMA table_info(users)")}
        assert "matrix_id" in cols
        assert cols["tg_id"][3] == 0  # notnull flag == 0 -> nullable
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "matrix_deliveries" in tables
    finally:
        con.close()
```

Append to `tests/test_repositories.py`:

```python
async def test_user_needs_at_least_one_identity(session):
    from sqlalchemy.exc import IntegrityError

    from bot.db.models import User

    session.add(User(full_name="nobody"))
    with pytest.raises(IntegrityError):
        await session.flush()
    await session.rollback()

    session.add(User(matrix_id="@nodir:example.uz", full_name="Nodir"))
    await session.flush()  # tg_id may be NULL when matrix_id is present
```

Make sure `import pytest` exists at the top of `tests/test_repositories.py`.

- [ ] **Step 2: Run tests to verify they fail**

Run: Docker test command with `pytest -q tests/test_migrate.py tests/test_repositories.py`.
Expected: FAIL — `assert "matrix_id" in cols` and `TypeError`/`IntegrityError` mismatch (User has no `matrix_id`).

- [ ] **Step 3: Update the ORM model**

In `bot/db/models.py`, add `CheckConstraint` to the `sqlalchemy` import list. Replace the `User` class body's identity columns:

```python
class User(TimestampMixin, Base):
    __tablename__ = "users"
    __table_args__ = (
        # A user is either a Telegram user, a Matrix user, or both — never neither.
        CheckConstraint("tg_id IS NOT NULL OR matrix_id IS NOT NULL", name="ck_users_identity"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Telegram identity. NULL for users provisioned from a Matrix room.
    tg_id: Mapped[int | None] = mapped_column(
        BigInteger, unique=True, index=True, nullable=True
    )
    # Matrix identity (@user:server). NULL for Telegram-only users.
    matrix_id: Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True)
    username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    full_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    language: Mapped[str | None] = mapped_column(String(16), nullable=True)

    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    resp_appeal: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    resp_corruption: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
```

After `class SubmissionDelivery`, add:

```python
class MatrixDelivery(Base):
    """An event the bot posted to a Matrix room about a submission.

    ``kind='card'`` is the editable card; ``attachment`` and ``note`` are the
    other bot events for the same submission. A reply to ANY of them resolves
    to the submission (see MatrixDeliveryRepository.submission_id_for).
    """

    __tablename__ = "matrix_deliveries"
    __table_args__ = (UniqueConstraint("room_id", "event_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    submission_id: Mapped[int] = mapped_column(
        ForeignKey("submissions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    room_id: Mapped[str] = mapped_column(String(255), nullable=False)
    event_id: Mapped[str] = mapped_column(String(255), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default="card")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
```

- [ ] **Step 4: Write the migration**

Create `alembic/versions/0002_matrix.py`:

```python
"""matrix bridge: users.matrix_id, nullable tg_id, matrix_deliveries

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-11
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # batch mode so the same revision works on SQLite (tests) and Postgres.
    with op.batch_alter_table("users") as batch:
        batch.alter_column("tg_id", existing_type=sa.BigInteger(), nullable=True)
        batch.add_column(sa.Column("matrix_id", sa.String(length=255), nullable=True))
        batch.create_unique_constraint("uq_users_matrix_id", ["matrix_id"])
        batch.create_check_constraint(
            "ck_users_identity", "tg_id IS NOT NULL OR matrix_id IS NOT NULL"
        )

    op.create_table(
        "matrix_deliveries",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("submission_id", sa.Integer(), nullable=False),
        sa.Column("room_id", sa.String(length=255), nullable=False),
        sa.Column("event_id", sa.String(length=255), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["submission_id"], ["submissions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("room_id", "event_id"),
    )
    op.create_index(
        op.f("ix_matrix_deliveries_submission_id"), "matrix_deliveries", ["submission_id"]
    )


def downgrade() -> None:
    # tg_id cannot become NOT NULL again while Matrix-only users exist.
    remaining = op.get_bind().execute(
        sa.text("SELECT COUNT(*) FROM users WHERE tg_id IS NULL")
    ).scalar()
    if remaining:
        raise RuntimeError(
            f"Cannot downgrade: {remaining} Matrix-only user(s) have no tg_id. "
            "Delete them first."
        )
    op.drop_index(op.f("ix_matrix_deliveries_submission_id"), table_name="matrix_deliveries")
    op.drop_table("matrix_deliveries")
    with op.batch_alter_table("users") as batch:
        batch.drop_constraint("ck_users_identity", type_="check")
        batch.drop_constraint("uq_users_matrix_id", type_="unique")
        batch.drop_column("matrix_id")
        batch.alter_column("tg_id", existing_type=sa.BigInteger(), nullable=False)
```

Note: `server_default=sa.text("now()")` matches revision 0001's style; SQLite accepts it as a default expression only at DDL time (it is never evaluated there in tests because the ORM sets `created_at` via `func.now()` on insert). If SQLite rejects it in `test_migrate`, use `sa.text("CURRENT_TIMESTAMP")` instead — it is valid on both engines.

- [ ] **Step 5: Run tests to verify they pass**

Run: Docker test command with `pytest -q tests/test_migrate.py tests/test_repositories.py`.
Expected: PASS.

- [ ] **Step 6: Run the whole suite**

Run the full Docker test command. Expected: `ruff` clean, everything that passed before still passes (134 + new), same 3 concurrency errors.

- [ ] **Step 7: Commit**

```bash
git add bot/db/models.py alembic/versions/0002_matrix.py tests/test_migrate.py tests/test_repositories.py
git commit -m "Allow Matrix-only users and record Matrix deliveries"
```

---

### Task 3: Repositories — Matrix users and deliveries

**Files:**
- Modify: `bot/db/repositories.py` (`UserRepository` additions, `responsibles_for` filter, new `MatrixDeliveryRepository`)
- Modify: `bot/handlers/admin.py:96` (listing fallback)
- Test: `tests/test_matrix_users.py` (create)

**Interfaces:**
- Produces:
  - `UserRepository.get_by_matrix_id(matrix_id: str) -> User | None`
  - `UserRepository.get_or_create_matrix(matrix_id: str, full_name: str | None, type_: SubmissionType) -> tuple[User, bool]` — creates on first sight, always ensures the `resp_<type>` flag for `type_` is set.
  - `UserRepository.responsibles_for(type_)` — now excludes `tg_id IS NULL`.
  - `MatrixDeliveryRepository(session)` with `add(submission_id, room_id, event_id, kind) -> MatrixDelivery`, `submission_id_for(room_id, event_id) -> int | None`, `card_for(submission_id) -> MatrixDelivery | None`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_matrix_users.py`:

```python
"""Matrix-only users: provisioned on first action, one per Matrix id, role from
the room type, never a Telegram recipient.
"""
from bot.db.models import SubmissionType, User
from bot.db.repositories import MatrixDeliveryRepository, UserRepository


async def test_first_action_creates_user_with_room_role(session):
    repo = UserRepository(session)
    user, created = await repo.get_or_create_matrix(
        "@nodir:example.uz", "Nodir", SubmissionType.appeal
    )
    assert created is True
    assert user.matrix_id == "@nodir:example.uz"
    assert user.tg_id is None
    assert user.full_name == "Nodir"
    assert user.resp_appeal is True
    assert user.resp_corruption is False
    assert user.is_admin is False


async def test_second_action_reuses_user(session):
    repo = UserRepository(session)
    first, _ = await repo.get_or_create_matrix("@nodir:example.uz", "Nodir", SubmissionType.appeal)
    second, created = await repo.get_or_create_matrix(
        "@nodir:example.uz", "Nodir N.", SubmissionType.appeal
    )
    assert created is False
    assert second.id == first.id


async def test_member_of_both_rooms_gets_both_flags(session):
    repo = UserRepository(session)
    await repo.get_or_create_matrix("@nodir:example.uz", "Nodir", SubmissionType.appeal)
    user, _ = await repo.get_or_create_matrix(
        "@nodir:example.uz", "Nodir", SubmissionType.corruption
    )
    assert user.resp_appeal is True and user.resp_corruption is True


async def test_matrix_users_are_not_telegram_recipients(session):
    repo = UserRepository(session)
    await repo.get_or_create_matrix("@nodir:example.uz", "Nodir", SubmissionType.appeal)
    tg = User(tg_id=100, resp_appeal=True)
    session.add(tg)
    await session.flush()

    recipients = await repo.responsibles_for(SubmissionType.appeal)
    assert [u.tg_id for u in recipients] == [100]


async def test_matrix_deliveries_resolve_replies(session):
    from bot.db.models import Submission, SubmissionStatus

    sub = Submission(
        public_id="ABCDEFGH", ticket_number="OBR-2026-0001", type=SubmissionType.appeal,
        status=SubmissionStatus.new, text="t",
    )
    session.add(sub)
    await session.flush()

    repo = MatrixDeliveryRepository(session)
    card = await repo.add(sub.id, "!room:x", "$card", "card")
    await repo.add(sub.id, "!room:x", "$att1", "attachment")

    assert await repo.submission_id_for("!room:x", "$card") == sub.id
    assert await repo.submission_id_for("!room:x", "$att1") == sub.id
    assert await repo.submission_id_for("!room:x", "$unknown") is None
    assert (await repo.card_for(sub.id)).event_id == card.event_id
```

- [ ] **Step 2: Run tests to verify they fail**

Run: Docker test command with `pytest -q tests/test_matrix_users.py`.
Expected: FAIL — `ImportError: cannot import name 'MatrixDeliveryRepository'`.

- [ ] **Step 3: Implement the repositories**

In `bot/db/repositories.py`, add `MatrixDelivery` to the `bot.db.models` import. In `UserRepository`, after `get_by_tg_id`, add:

```python
    async def get_by_matrix_id(self, matrix_id: str) -> User | None:
        return await self.session.scalar(select(User).where(User.matrix_id == matrix_id))

    async def get_or_create_matrix(
        self, matrix_id: str, full_name: str | None, type_: SubmissionType
    ) -> tuple[User, bool]:
        """Provision a room member as a User on first sight.

        Room membership is the only grant: a member of the appeal room gets
        resp_appeal, of the corruption room resp_corruption. The flag is ensured
        on every call, so one person acting in both rooms ends up with both.
        Matrix-only users are never admins.
        """
        user = await self.get_by_matrix_id(matrix_id)
        created = False
        if user is None:
            user = User(matrix_id=matrix_id, full_name=full_name)
            self.session.add(user)
            try:
                await self.session.flush()
            except IntegrityError:
                await self.session.rollback()
                existing = await self.get_by_matrix_id(matrix_id)
                if existing is None:
                    raise
                user = existing
            else:
                created = True
        flag = "resp_appeal" if type_ == SubmissionType.appeal else "resp_corruption"
        if not getattr(user, flag):
            setattr(user, flag, True)
            await self.session.flush()
        return user, created
```

Replace `responsibles_for`:

```python
    async def responsibles_for(self, type_: SubmissionType) -> list[User]:
        """Responsibles reachable in Telegram. Matrix-only users (tg_id NULL)
        get their cards through the Matrix bridge instead."""
        col = User.resp_appeal if type_ == SubmissionType.appeal else User.resp_corruption
        return list(
            await self.session.scalars(
                select(User).where(col.is_(True), User.tg_id.is_not(None))
            )
        )
```

At the end of the file add:

```python
class MatrixDeliveryRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add(
        self, submission_id: int, room_id: str, event_id: str, kind: str
    ) -> MatrixDelivery:
        row = MatrixDelivery(
            submission_id=submission_id, room_id=room_id, event_id=event_id, kind=kind
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def submission_id_for(self, room_id: str, event_id: str) -> int | None:
        return await self.session.scalar(
            select(MatrixDelivery.submission_id).where(
                MatrixDelivery.room_id == room_id, MatrixDelivery.event_id == event_id
            )
        )

    async def card_for(self, submission_id: int) -> MatrixDelivery | None:
        return await self.session.scalar(
            select(MatrixDelivery)
            .where(MatrixDelivery.submission_id == submission_id, MatrixDelivery.kind == "card")
            .order_by(MatrixDelivery.id.desc())
            .limit(1)
        )
```

- [ ] **Step 4: Fix the admin listing for users without `tg_id`**

In `bot/handlers/admin.py` line 96, replace

```python
        name = escape(u.full_name) if u.full_name else str(u.tg_id)
```

with

```python
        name = escape(u.full_name) if u.full_name else str(u.tg_id or u.matrix_id)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: Docker test command with `pytest -q tests/test_matrix_users.py tests/test_repositories.py tests/test_responsible_admin.py`.
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add bot/db/repositories.py bot/handlers/admin.py tests/test_matrix_users.py
git commit -m "Provision Matrix room members as users; track Matrix deliveries"
```

---

### Task 4: `SubmissionActions` service and thin Telegram handlers

**Files:**
- Create: `bot/services/actions.py`
- Modify: `bot/handlers/responsible.py` (rewrite handlers as adapters)
- Modify: `bot/factory.py` (`dp["card_sinks"] = []`)
- Modify: `tests/test_responsible_admin.py` (`env` fixture: `dp["card_sinks"] = []`)
- Test: `tests/test_actions.py` (create)

**Interfaces:**
- Consumes: `SubmissionService`, `SubmissionRepository.try_claim/close/record_status_event`, `AuditRepository.log`, `can_handle_type`, `safe_send`.
- Produces:

  ```python
  class CardSink(Protocol):
      async def announce(self, session: AsyncSession, submission_id: int) -> bool: ...
      async def refresh(self, session: AsyncSession, submission_id: int) -> None: ...

  @dataclass
  class TakeResult:
      won: bool
      assignee_name: str   # HTML-escaped; "—" when unknown

  def display_name(user: User | None) -> str
  async def author_locale(session, sub) -> str

  class SubmissionActions:
      def __init__(self, session, bot, core, cipher, default_locale, sinks=())
      async def authorize(self, submission_id, user, *, require_owner=False) -> Submission | None
      async def take(self, sub, user) -> TakeResult
      async def close(self, sub, user) -> bool
      async def reply(self, sub, user, text) -> bool
  ```

- [ ] **Step 1: Write the failing tests**

Create `tests/test_actions.py`:

```python
"""SubmissionActions: the single implementation of take / close / reply used by
both Telegram handlers and the Matrix bridge.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select

from bot.db.models import (
    AuditLog,
    Submission,
    SubmissionResponse,
    SubmissionStatus,
    SubmissionType,
    User,
)
from bot.security.crypto import AnonCipher
from bot.services.actions import SubmissionActions, TakeResult
from bot.services.submissions import SubmissionInput, SubmissionService

_core = SimpleNamespace(get=lambda key, locale=None, **kw: key)


class _Sink:
    def __init__(self):
        self.refreshed = []
        self.announced = []

    async def announce(self, session, submission_id):
        self.announced.append(submission_id)
        return True

    async def refresh(self, session, submission_id):
        self.refreshed.append(submission_id)


async def _officer(session, *, tg_id=500, admin=False):
    user = User(tg_id=tg_id, full_name="Officer", is_admin=admin, resp_appeal=True)
    session.add(user)
    await session.flush()
    return user


async def _submission(session, cipher, *, anonymous=False, author_tg=900):
    author_uid = None
    if not anonymous:
        author = User(tg_id=author_tg, language="ru")
        session.add(author)
        await session.flush()
        author_uid = author.id
    svc = SubmissionService(session, cipher)
    sub = await svc.create(
        SubmissionInput(
            type=SubmissionType.corruption if anonymous else SubmissionType.appeal,
            text="report", is_anonymous=anonymous,
            author_tg_id=author_tg, author_user_id=author_uid,
        )
    )
    if anonymous:
        # Officer must be allowed to act on corruption too.
        pass
    await session.commit()
    return sub


@pytest.fixture
def cipher():
    return AnonCipher(Fernet.generate_key().decode())


@pytest.fixture
def bot():
    b = AsyncMock()
    b.send_message.return_value = SimpleNamespace(message_id=1)
    return b


async def test_take_wins_records_and_refreshes(session, cipher, bot):
    officer = await _officer(session)
    sub = await _submission(session, cipher)
    sink = _Sink()
    actions = SubmissionActions(session, bot, _core, cipher, "ru", sinks=[sink])

    result = await actions.take(sub, officer)

    assert result == TakeResult(won=True, assignee_name="Officer")
    fresh = await session.get(Submission, sub.id, populate_existing=True)
    assert fresh.status == SubmissionStatus.in_progress
    assert fresh.assigned_to_user_id == officer.id
    audits = list(await session.scalars(select(AuditLog).where(AuditLog.action == "status_change")))
    assert any(a.meta == "new->in_progress" and a.actor_user_id == officer.id for a in audits)
    assert sink.refreshed == [sub.id]


async def test_take_loses_reports_current_assignee(session, cipher, bot):
    officer = await _officer(session)
    other = User(tg_id=601, full_name="Other", resp_appeal=True)
    session.add(other)
    await session.flush()
    sub = await _submission(session, cipher)
    actions = SubmissionActions(session, bot, _core, cipher, "ru")
    assert (await actions.take(sub, other)).won is True

    result = await actions.take(sub, officer)

    assert result.won is False
    assert result.assignee_name == "Other"


async def test_authorize_enforces_type_and_ownership(session, cipher, bot):
    officer = await _officer(session)  # resp_appeal only
    other = User(tg_id=601, resp_appeal=True)
    session.add(other)
    await session.flush()
    sub = await _submission(session, cipher)
    actions = SubmissionActions(session, bot, _core, cipher, "ru")

    assert (await actions.authorize(sub.id, officer)) is not None
    await actions.take(sub, other)
    # Not the assignee -> may not reply/close.
    assert (await actions.authorize(sub.id, officer, require_owner=True)) is None
    # Admin may.
    officer.is_admin = True
    assert (await actions.authorize(sub.id, officer, require_owner=True)) is not None

    anon = await _submission(session, cipher, anonymous=True, author_tg=4242)
    officer.is_admin = False
    assert (await actions.authorize(anon.id, officer)) is None  # no resp_corruption


async def test_close_notifies_author_and_refreshes(session, cipher, bot):
    officer = await _officer(session)
    sub = await _submission(session, cipher, author_tg=900)
    sink = _Sink()
    actions = SubmissionActions(session, bot, _core, cipher, "ru", sinks=[sink])

    assert await actions.close(sub, officer) is True
    assert await actions.close(sub, officer) is False  # already closed

    fresh = await session.get(Submission, sub.id, populate_existing=True)
    assert fresh.status == SubmissionStatus.closed
    assert any(c.args[0] == 900 for c in bot.send_message.await_args_list)
    assert sink.refreshed == [sub.id]


async def test_reply_reaches_anonymous_author(session, cipher, bot):
    officer = await _officer(session)
    officer.resp_corruption = True
    sub = await _submission(session, cipher, anonymous=True, author_tg=4242)
    actions = SubmissionActions(session, bot, _core, cipher, "ru")

    assert await actions.reply(sub, officer, "We are on it.") is True

    resp = (await session.scalars(select(SubmissionResponse))).one()
    assert resp.text == "We are on it." and resp.responder_user_id == officer.id
    assert any(c.args[0] == 4242 for c in bot.send_message.await_args_list)


async def test_reply_returns_false_when_author_blocked_bot(session, cipher, bot):
    from aiogram.exceptions import TelegramForbiddenError

    officer = await _officer(session)
    sub = await _submission(session, cipher, author_tg=900)
    bot.send_message.side_effect = TelegramForbiddenError(method=None, message="blocked")
    actions = SubmissionActions(session, bot, _core, cipher, "ru")

    assert await actions.reply(sub, officer, "hello") is False
    # The response is still recorded — the operator's work is not lost.
    assert (await session.scalars(select(SubmissionResponse))).one().text == "hello"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: Docker test command with `pytest -q tests/test_actions.py`.
Expected: FAIL — `ModuleNotFoundError: No module named 'bot.services.actions'`.

- [ ] **Step 3: Create the service**

Create `bot/services/actions.py`:

```python
"""Submission actions — the ONE place take / close / reply are implemented.

Telegram handlers (handlers/responsible.py) and the Matrix bridge
(matrix/bridge.py) are adapters over this class. Authorization is derived
server-side from the acting User (never from client-supplied data), status
changes are atomic (repository), and every operation commits its rows BEFORE
any network fan-out so a responsible never learns about state a later
rollback would erase.

Card sinks: other channels that show the submission (today: the Matrix room)
register a CardSink so a status change in one channel is redrawn in all.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from aiogram import Bot
from aiogram_i18n.cores import BaseCore
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import Submission, SubmissionResponse, User
from bot.db.repositories import AuditRepository
from bot.filters.roles import can_handle_type
from bot.security.crypto import AnonCipher
from bot.services.submissions import SubmissionService, safe_send
from bot.utils.text import escape


class CardSink(Protocol):
    """A channel that displays submission cards and needs redraws."""

    async def announce(self, session: AsyncSession, submission_id: int) -> bool:
        """Post a new submission. Returns True if at least one card went out."""

    async def refresh(self, session: AsyncSession, submission_id: int) -> None:
        """Redraw the card after a status change."""


@dataclass(frozen=True)
class TakeResult:
    won: bool
    assignee_name: str  # HTML-escaped display name of whoever holds it now


def display_name(user: User | None) -> str:
    return escape(user.full_name) if user is not None and user.full_name else "—"


async def author_locale(session: AsyncSession, sub: Submission) -> str:
    """Applicant's locale for non-anonymous submissions; default ru otherwise."""
    if sub.author_user_id is not None:
        author = await session.get(User, sub.author_user_id)
        if author and author.language:
            return author.language
    return "ru"


class SubmissionActions:
    def __init__(
        self,
        session: AsyncSession,
        bot: Bot,
        core: BaseCore,
        cipher: AnonCipher,
        default_locale: str,
        sinks: tuple[CardSink, ...] | list[CardSink] = (),
    ) -> None:
        self.session = session
        self.bot = bot
        self.core = core
        self.svc = SubmissionService(session, cipher)
        self.default_locale = default_locale
        self.sinks = list(sinks)

    async def authorize(
        self, submission_id: int, user: User, *, require_owner: bool = False
    ) -> Submission | None:
        """The submission if ``user`` may act on it, else None.

        The user must be responsible for THIS submission's type (or admin).
        With ``require_owner`` (reply/close) they must also be the assignee or an
        admin — first-claim ownership, so colleagues can't act on each other's cases.
        """
        sub = await self.svc.repo.get(submission_id)
        if sub is None or not can_handle_type(user, sub.type):
            return None
        if require_owner and not user.is_admin:
            if sub.assigned_to_user_id not in (None, user.id):
                return None
        return sub

    async def take(self, sub: Submission, user: User) -> TakeResult:
        won = await self.svc.repo.try_claim(sub.id, user.id)
        if not won:
            fresh = await self.session.get(Submission, sub.id, populate_existing=True)
            assignee = (
                await self.session.get(User, fresh.assigned_to_user_id)
                if fresh is not None and fresh.assigned_to_user_id else None
            )
            return TakeResult(won=False, assignee_name=display_name(assignee))

        await self.svc.repo.record_status_event(sub.id, user.id, "new", "in_progress")
        await AuditRepository(self.session).log(
            action="status_change", actor_user_id=user.id,
            target=f"submission:{sub.id}", meta="new->in_progress",
        )
        await self.session.commit()

        name = display_name(user)
        await self.svc.update_all_cards(self.bot, self.core, sub, "card-assigned", name=name)
        await self.session.commit()
        await self._refresh(sub.id)
        return TakeResult(won=True, assignee_name=name)

    async def close(self, sub: Submission, user: User) -> bool:
        prev = await self.svc.repo.close(sub.id, user.id)
        if prev is None:
            return False
        await self.svc.repo.record_status_event(sub.id, user.id, prev, "closed")
        await AuditRepository(self.session).log(
            action="status_change", actor_user_id=user.id,
            target=f"submission:{sub.id}", meta=f"{prev}->closed",
        )
        await self.session.commit()

        await self.svc.update_all_cards(self.bot, self.core, sub, "status-closed")
        chat_id = await self.svc.resolve_author_chat_id(sub)
        if chat_id is not None:
            locale = await author_locale(self.session, sub)
            text = self.core.get("submission-closed-notify", locale, public_id=sub.public_id)
            await safe_send(self.bot, chat_id, text)
        await self.session.commit()
        await self._refresh(sub.id)
        return True

    async def reply(self, sub: Submission, user: User, text: str) -> bool:
        """Record the response and deliver it. False if the applicant is unreachable
        (blocked the bot / no chat ref) — the response row is kept either way."""
        self.session.add(
            SubmissionResponse(submission_id=sub.id, responder_user_id=user.id, text=text)
        )
        await AuditRepository(self.session).log(
            action="reply", actor_user_id=user.id, target=f"submission:{sub.id}"
        )
        await self.session.commit()

        chat_id = await self.svc.resolve_author_chat_id(sub)
        if chat_id is None:
            return False
        locale = await author_locale(self.session, sub)
        header = self.core.get("reply-to-author", locale, public_id=sub.public_id)
        body = self.core.get("reply-to-author-body", locale, text=escape(text))
        return await safe_send(self.bot, chat_id, f"{header}\n{body}")

    async def _refresh(self, submission_id: int) -> None:
        for sink in self.sinks:
            try:
                await sink.refresh(self.session, submission_id)
            except Exception:  # noqa: BLE001 — one channel must not break the action
                import logging

                logging.getLogger(__name__).exception("card sink refresh failed")
```

- [ ] **Step 4: Run the new tests**

Run: Docker test command with `pytest -q tests/test_actions.py`.
Expected: PASS.

- [ ] **Step 5: Rewrite `handlers/responsible.py` as an adapter**

Replace the whole file with:

```python
"""Responsible-person reactions: take in progress, reply, close.

Thin adapter: parse the CallbackQuery, call SubmissionActions (the single
implementation shared with the Matrix bridge), answer the query, and redraw
the registry screen if the click came from there. Authorization lives in
SubmissionActions.authorize — callback data is never trusted for authz.
"""
from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from aiogram_i18n import I18nContext

from bot.config import Settings
from bot.db.models import User
from bot.filters.roles import can_handle_type
from bot.handlers import registry
from bot.handlers.states import ResponseForm
from bot.keyboards.inline import ReactionCb
from bot.security.crypto import AnonCipher
from bot.services.actions import SubmissionActions

router = Router(name="responsible")


def _actions(session, settings: Settings, bot, core, card_sinks) -> SubmissionActions:
    return SubmissionActions(
        session, bot, core, AnonCipher(settings.anon_enc_key),
        settings.default_locale, sinks=card_sinks or (),
    )


@router.callback_query(ReactionCb.filter(F.action == "take"))
async def on_take(
    query: CallbackQuery, callback_data: ReactionCb, db_user: User, session,
    i18n: I18nContext, settings: Settings, card_sinks: list | None = None,
) -> None:
    actions = _actions(session, settings, query.bot, i18n.core, card_sinks)
    sub = await actions.authorize(callback_data.submission_id, db_user)
    if sub is None:
        await query.answer(i18n.get("admin-only"), show_alert=True)
        return
    result = await actions.take(sub, db_user)
    if not result.won:
        await query.answer(i18n.get("cb-already-taken", name=result.assignee_name), show_alert=True)
        return
    # If the click came from the registry, redraw that screen too — the card
    # update inside take() only touches the push cards.
    await registry.refresh_detail(query, i18n, session, sub.id)
    await query.answer(i18n.get("cb-taken"))


@router.callback_query(ReactionCb.filter(F.action == "close"))
async def on_close(
    query: CallbackQuery, callback_data: ReactionCb, db_user: User, session,
    i18n: I18nContext, settings: Settings, card_sinks: list | None = None,
) -> None:
    actions = _actions(session, settings, query.bot, i18n.core, card_sinks)
    sub = await actions.authorize(callback_data.submission_id, db_user, require_owner=True)
    if sub is None:
        await query.answer(i18n.get("admin-only"), show_alert=True)
        return
    await actions.close(sub, db_user)  # False = already closed; same answer either way
    await registry.refresh_detail(query, i18n, session, sub.id)
    await query.answer(i18n.get("cb-closed"))


@router.callback_query(ReactionCb.filter(F.action == "reply"))
async def on_reply_start(
    query: CallbackQuery, callback_data: ReactionCb, db_user: User, session,
    i18n: I18nContext, state: FSMContext, settings: Settings,
    card_sinks: list | None = None,
) -> None:
    actions = _actions(session, settings, query.bot, i18n.core, card_sinks)
    sub = await actions.authorize(callback_data.submission_id, db_user, require_owner=True)
    if sub is None:
        await query.answer(i18n.get("admin-only"), show_alert=True)
        return
    await state.set_state(ResponseForm.text)
    await state.update_data(submission_id=sub.id)
    if query.message is not None:
        await query.message.answer(i18n.get("reply-ask"))
    else:
        # Card too old to carry a message; prompt via the bot directly.
        await query.bot.send_message(query.from_user.id, i18n.get("reply-ask"))
    await query.answer()


@router.message(ResponseForm.text, F.text)
async def on_reply_text(
    message: Message, db_user: User, session, i18n: I18nContext,
    state: FSMContext, settings: Settings, card_sinks: list | None = None,
) -> None:
    data = await state.get_data()
    submission_id = data["submission_id"]
    await state.clear()
    actions = _actions(session, settings, message.bot, i18n.core, card_sinks)
    sub = await actions.svc.repo.get(submission_id)
    if sub is None or not can_handle_type(db_user, sub.type):
        await message.answer(i18n.get("admin-only"))
        return
    await actions.reply(sub, db_user, message.text)
    await message.answer(i18n.get("reply-sent"))
```

Behavioral parity notes (verify against the old file while editing): `on_take` lost-claim alert, `on_close` answering `cb-closed` even when already closed, `on_reply_text` not re-checking ownership (only type) — all preserved exactly.

- [ ] **Step 6: Expose `card_sinks` from the dispatcher context**

In `bot/factory.py`, right after `dp["i18n_core"] = core`, add:

```python
    # Channels that display submission cards besides Telegram push cards. The
    # Matrix bridge appends itself at startup (see bot/__main__.py); handlers
    # receive this list by name and pass it to SubmissionActions.
    dp["card_sinks"] = []
```

In `tests/test_responsible_admin.py`, in the `env` fixture after `dp["settings"] = settings`, add:

```python
    dp["card_sinks"] = []
```

- [ ] **Step 7: Run the handler and registry tests — behaviour must be unchanged**

Run: Docker test command with `pytest -q tests/test_responsible_admin.py tests/test_registry_refresh.py tests/test_registry_flow.py tests/test_handlers_flow.py tests/test_actions.py`.
Expected: PASS, no test edited except the one fixture line.

- [ ] **Step 8: Run the full suite and ruff, then commit**

Run the full Docker test command. Expected: ruff clean; all previous tests pass plus 6 new.

```bash
git add bot/services/actions.py bot/handlers/responsible.py bot/factory.py tests/test_actions.py tests/test_responsible_admin.py
git commit -m "Extract take/close/reply into SubmissionActions; handlers become adapters"
```

---

### Task 5: Room locale strings and the card renderer

**Files:**
- Modify: `bot/locales/{ru,kaa,uz_cyrl,uz_latn,en}/LC_MESSAGES/bot.ftl` (append the same 12 keys)
- Create: `bot/matrix/__init__.py` (empty), `bot/matrix/render.py`
- Test: `tests/test_matrix_render.py` (create)

**Interfaces:**
- Produces (`bot/matrix/render.py`):
  - `html_body(text: str) -> str` — `\n` → `<br/>`
  - `plain_body(text: str) -> str` — tags stripped, entities unescaped
  - `strip_reply_fallback(body: str) -> str`
  - `parse_command(body: str) -> tuple[str | None, str]` — `"!olish ABC"` → `("olish", "ABC")`
  - `render_room_card(core, locale, sub, *, assignee_name: str | None, attachment_count: int, failed_attachments: int = 0) -> str`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_matrix_render.py`:

```python
"""Room card rendering and message parsing for the Matrix bridge."""
from pathlib import Path

import pytest
from aiogram_i18n.cores.fluent_runtime_core import FluentRuntimeCore

from bot.db.models import Submission, SubmissionStatus, SubmissionType
from bot.matrix import render

_LOCALES = str(Path("bot/locales") / "{locale}" / "LC_MESSAGES")


@pytest.fixture
async def core():
    c = FluentRuntimeCore(path=_LOCALES)
    await c.startup()
    return c


def _sub(**kw):
    base = dict(
        id=1, public_id="ABCDEFGH", ticket_number="OBR-2026-0001",
        type=SubmissionType.appeal, status=SubmissionStatus.new,
        is_anonymous=False, full_name="Ali <b>Valiyev</b>", phone="+998901234567",
        text="Suv <yo'q>",
    )
    base.update(kw)
    return Submission(**base)


def test_parse_command():
    assert render.parse_command("!olish") == ("olish", "")
    assert render.parse_command("  !Yopish ABCDEFGH ") == ("yopish", "ABCDEFGH")
    assert render.parse_command("oddiy matn") == (None, "")
    assert render.parse_command("!") == (None, "")


def test_strip_reply_fallback():
    body = "> <@bot:x> card line 1\n> card line 2\n\nMy answer\nsecond line"
    assert render.strip_reply_fallback(body) == "My answer\nsecond line"
    assert render.strip_reply_fallback("plain") == "plain"


def test_html_and_plain_bodies():
    assert render.html_body("a\nb") == "a<br/>b"
    assert render.plain_body("<b>x</b> &lt;y&gt;") == "x <y>"


async def test_card_new_shows_take_hint_and_escapes(core):
    text = render.render_room_card(
        core, "uz_latn", _sub(), assignee_name=None, attachment_count=2
    )
    assert "ABCDEFGH" in text
    assert "&lt;b&gt;Valiyev&lt;/b&gt;" in text  # user text is escaped
    assert "!olish" in text
    assert "!yopish" not in text  # cannot close what nobody took
    assert "2" in text  # attachment count


async def test_card_in_progress_shows_owner_and_close(core):
    text = render.render_room_card(
        core, "uz_latn", _sub(status=SubmissionStatus.in_progress),
        assignee_name="Nodir", attachment_count=0,
    )
    assert "Nodir" in text
    assert "!yopish" in text
    assert "!olish" not in text


async def test_card_closed_offers_only_card(core):
    text = render.render_room_card(
        core, "uz_latn", _sub(status=SubmissionStatus.closed),
        assignee_name="Nodir", attachment_count=0,
    )
    assert "!karta" in text
    assert "!olish" not in text and "!yopish" not in text


async def test_anonymous_card_has_no_pii(core):
    text = render.render_room_card(
        core, "uz_latn",
        _sub(type=SubmissionType.corruption, is_anonymous=True, full_name=None, phone=None),
        assignee_name=None, attachment_count=0,
    )
    assert "+998" not in text and "Valiyev" not in text


async def test_every_locale_renders(core):
    for locale in ("ru", "kaa", "uz_cyrl", "uz_latn", "en"):
        text = render.render_room_card(core, locale, _sub(), assignee_name=None, attachment_count=1)
        assert "ABCDEFGH" in text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: Docker test command with `pytest -q tests/test_matrix_render.py`.
Expected: FAIL — `ModuleNotFoundError: No module named 'bot.matrix'`.

- [ ] **Step 3: Add the locale keys (all five files)**

Append to `bot/locales/uz_latn/LC_MESSAGES/bot.ftl`:

```fluent

## Matrix (Element) room
mx-attachments = 📎 Ilovalar: { $count }
mx-attachment-failed = ⚠️ { $count } ta ilovani yuklab boʻlmadi (20 MB dan katta yoki xato)
mx-assignee = 👤 Ijrochi: { $name }
mx-hint-take = ▶️ Ishga olish: shu xabarga reply qilib <code>!olish</code>
mx-hint-reply = ✍️ Javob: shu xabarga reply qilib matn yozing
mx-hint-close = ✅ Yopish: shu xabarga reply qilib <code>!yopish</code>
mx-hint-card = 🔄 Kartochka: <code>!karta</code>
mx-not-owner = ⚠️ Bu murojaatni { $name } koʻrib chiqmoqda — faqat u yopishi yoki javob berishi mumkin
mx-already-closed = ℹ️ Murojaat allaqachon yopilgan
mx-forbidden = ⚠️ Bu turdagi murojaatlar uchun sizda huquq yoʻq
mx-reply-undeliverable = ⚠️ Murojaatchiga yetkazib boʻlmadi (u botni bloklagan boʻlishi mumkin). Javob saqlandi.
mx-help = <b>Buyruqlar</b> (kartochkaga reply qilib):<br/>• matn — murojaatchiga javob<br/>• <code>!olish</code> — ishga olish<br/>• <code>!yopish</code> — yopish<br/>• <code>!karta</code> — kartochkani qayta koʻrsatish<br/>• <code>!yordam</code> — shu roʻyxat
```

Append to `bot/locales/uz_cyrl/LC_MESSAGES/bot.ftl`:

```fluent

## Matrix (Element) room
mx-attachments = 📎 Иловалар: { $count }
mx-attachment-failed = ⚠️ { $count } та иловани юклаб бўлмади (20 MB дан катта ёки хато)
mx-assignee = 👤 Ижрочи: { $name }
mx-hint-take = ▶️ Ишга олиш: шу хабарга reply қилиб <code>!olish</code>
mx-hint-reply = ✍️ Жавоб: шу хабарга reply қилиб матн ёзинг
mx-hint-close = ✅ Ёпиш: шу хабарга reply қилиб <code>!yopish</code>
mx-hint-card = 🔄 Карточка: <code>!karta</code>
mx-not-owner = ⚠️ Бу мурожаатни { $name } кўриб чиқмоқда — фақат у ёпиши ёки жавоб бериши мумкин
mx-already-closed = ℹ️ Мурожаат аллақачон ёпилган
mx-forbidden = ⚠️ Бу турдаги мурожаатлар учун сизда ҳуқуқ йўқ
mx-reply-undeliverable = ⚠️ Мурожаатчига етказиб бўлмади (у ботни блоклаган бўлиши мумкин). Жавоб сақланди.
mx-help = <b>Буйруқлар</b> (карточкага reply қилиб):<br/>• матн — мурожаатчига жавоб<br/>• <code>!olish</code> — ишга олиш<br/>• <code>!yopish</code> — ёпиш<br/>• <code>!karta</code> — карточкани қайта кўрсатиш<br/>• <code>!yordam</code> — шу рўйхат
```

Append to `bot/locales/ru/LC_MESSAGES/bot.ftl`:

```fluent

## Matrix (Element) room
mx-attachments = 📎 Вложения: { $count }
mx-attachment-failed = ⚠️ Не удалось загрузить вложений: { $count } (больше 20 МБ или ошибка)
mx-assignee = 👤 Исполнитель: { $name }
mx-hint-take = ▶️ Взять в работу: ответьте на это сообщение <code>!olish</code>
mx-hint-reply = ✍️ Ответ заявителю: ответьте на это сообщение текстом
mx-hint-close = ✅ Закрыть: ответьте на это сообщение <code>!yopish</code>
mx-hint-card = 🔄 Карточка: <code>!karta</code>
mx-not-owner = ⚠️ Заявку ведёт { $name } — закрыть или ответить может только он
mx-already-closed = ℹ️ Заявка уже закрыта
mx-forbidden = ⚠️ У вас нет прав на заявки этого типа
mx-reply-undeliverable = ⚠️ Не удалось доставить заявителю (возможно, бот заблокирован). Ответ сохранён.
mx-help = <b>Команды</b> (ответом на карточку):<br/>• текст — ответ заявителю<br/>• <code>!olish</code> — взять в работу<br/>• <code>!yopish</code> — закрыть<br/>• <code>!karta</code> — показать карточку заново<br/>• <code>!yordam</code> — эта справка
```

Append to `bot/locales/en/LC_MESSAGES/bot.ftl`:

```fluent

## Matrix (Element) room
mx-attachments = 📎 Attachments: { $count }
mx-attachment-failed = ⚠️ { $count } attachment(s) could not be uploaded (over 20 MB or an error)
mx-assignee = 👤 Assignee: { $name }
mx-hint-take = ▶️ Take: reply to this message with <code>!olish</code>
mx-hint-reply = ✍️ Answer the applicant: reply to this message with text
mx-hint-close = ✅ Close: reply to this message with <code>!yopish</code>
mx-hint-card = 🔄 Card: <code>!karta</code>
mx-not-owner = ⚠️ { $name } is handling this — only they can close or answer it
mx-already-closed = ℹ️ Already closed
mx-forbidden = ⚠️ You are not allowed to handle this type of submission
mx-reply-undeliverable = ⚠️ Could not deliver to the applicant (they may have blocked the bot). The answer was saved.
mx-help = <b>Commands</b> (as a reply to a card):<br/>• text — answer the applicant<br/>• <code>!olish</code> — take<br/>• <code>!yopish</code> — close<br/>• <code>!karta</code> — show the card again<br/>• <code>!yordam</code> — this help
```

Append to `bot/locales/kaa/LC_MESSAGES/bot.ftl`:

```fluent

## Matrix (Element) room
mx-attachments = 📎 Qosımshalar: { $count }
mx-attachment-failed = ⚠️ { $count } qosımshanı júklep bolmadı (20 MB dan úlken yamasa qáte)
mx-assignee = 👤 Orınlawshı: { $name }
mx-hint-take = ▶️ Jumısqa alıw: usı xabarǵa reply etip <code>!olish</code>
mx-hint-reply = ✍️ Juwap: usı xabarǵa reply etip tekst jazıń
mx-hint-close = ✅ Jabıw: usı xabarǵa reply etip <code>!yopish</code>
mx-hint-card = 🔄 Kartochka: <code>!karta</code>
mx-not-owner = ⚠️ Bul múrájatti { $name } qarap atır — tek ol jaba aladı yamasa juwap bere aladı
mx-already-closed = ℹ️ Múrájat álle qashan jabılǵan
mx-forbidden = ⚠️ Bul túrdegi múrájatlar ushın sizde huqıq joq
mx-reply-undeliverable = ⚠️ Múrájat iyesine jetkerip bolmadı (ol bottı bloklaǵan bolıwı múmkin). Juwap saqlandı.
mx-help = <b>Buyrıqlar</b> (kartochkaǵa reply etip):<br/>• tekst — múrájat iyesine juwap<br/>• <code>!olish</code> — jumısqa alıw<br/>• <code>!yopish</code> — jabıw<br/>• <code>!karta</code> — kartochkanı qayta kórsetiw<br/>• <code>!yordam</code> — usı dizim
```

Run `pytest -q tests/test_i18n.py` — it must pass (identical key sets in all five files). If it reports a missing key, the file you appended to has a typo in a key name.

- [ ] **Step 4: Create the renderer**

Create `bot/matrix/__init__.py` (empty, with a one-line docstring):

```python
"""Matrix (Element) bridge: cards for responsibles in rooms, actions by reply."""
```

Create `bot/matrix/render.py`:

```python
"""Rendering and parsing for the Matrix room.

The card body is the same ``render_card`` the Telegram push uses (so an
anonymous submission shows no PII here either), followed by the assignee,
attachment info and the commands available in the current status. Element has
no inline buttons, so every card spells out its own commands.

Matrix messages carry two bodies: ``formatted_body`` (HTML subset) and a plain
``body``. We author HTML (with Telegram-style tags plus <br/>) and derive the
plain text from it.
"""
from __future__ import annotations

import html
import re

from aiogram_i18n.cores import BaseCore

from bot.db.models import Submission, SubmissionStatus
from bot.services.submissions import render_card

COMMAND_PREFIX = "!"
_TAG = re.compile(r"<[^>]+>")


def html_body(text: str) -> str:
    return text.replace("\n", "<br/>")


def plain_body(text: str) -> str:
    return html.unescape(_TAG.sub("", text.replace("<br/>", "\n")))


def strip_reply_fallback(body: str) -> str:
    """Element prefixes a reply with the quoted original as '> ' lines."""
    lines = (body or "").split("\n")
    index = 0
    while index < len(lines) and lines[index].startswith(">"):
        index += 1
    while index < len(lines) and not lines[index].strip():
        index += 1
    return "\n".join(lines[index:]).strip()


def parse_command(body: str) -> tuple[str | None, str]:
    """'!olish ABC' -> ('olish', 'ABC'); plain text -> (None, '')."""
    text = (body or "").strip()
    if not text.startswith(COMMAND_PREFIX):
        return None, ""
    rest = text[len(COMMAND_PREFIX):].strip()
    if not rest:
        return None, ""
    name, _, args = rest.partition(" ")
    return name.lower(), args.strip()


def render_room_card(
    core: BaseCore,
    locale: str,
    sub: Submission,
    *,
    assignee_name: str | None,
    attachment_count: int,
    failed_attachments: int = 0,
) -> str:
    """Full card for the room in ``locale``. ``assignee_name`` must already be escaped."""
    type_label = core.get(f"type-{sub.type.value}", locale)
    lines = [render_card(core, locale, sub, type_label)]
    if assignee_name:
        lines.append(core.get("mx-assignee", locale, name=assignee_name))
    if attachment_count:
        lines.append(core.get("mx-attachments", locale, count=attachment_count))
    if failed_attachments:
        lines.append(core.get("mx-attachment-failed", locale, count=failed_attachments))
    lines.append("")
    if sub.status == SubmissionStatus.new:
        lines.append(core.get("mx-hint-take", locale))
        lines.append(core.get("mx-hint-reply", locale))
    elif sub.status == SubmissionStatus.in_progress:
        lines.append(core.get("mx-hint-reply", locale))
        lines.append(core.get("mx-hint-close", locale))
    lines.append(core.get("mx-hint-card", locale))
    return "\n".join(lines)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: Docker test command with `pytest -q tests/test_matrix_render.py tests/test_i18n.py`.
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add bot/locales bot/matrix/__init__.py bot/matrix/render.py tests/test_matrix_render.py
git commit -m "Add Matrix room card renderer and locale strings"
```

---

### Task 6: nio client wrapper

**Files:**
- Create: `bot/matrix/client.py`
- Test: `tests/test_matrix_client.py` (create — pure parsing only; the network parts are covered by the bridge tests through a fake)

**Interfaces:**
- Produces `MatrixClient` with:
  - `__init__(settings: MatrixSettings)`
  - `async connect() -> bool` — login (password or token), auto-join pending invites, register callbacks
  - `async run_sync() -> None` — blocks in `sync_forever`
  - `async close() -> None`
  - `user_id: str` (after connect), `started_ms: int`
  - `on_message(handler: Callable[[RoomEvent], Awaitable[None]])` where `RoomEvent` is a dataclass `(room_id, sender, event_id, body, reply_to, server_ts)`
  - `async send_html(room_id, html_text, *, reply_to: str | None = None) -> str` (event id or "")
  - `async edit_html(room_id, event_id, html_text) -> bool`
  - `async upload_file(room_id, data: bytes, name: str, mime: str) -> str` (event id or "")
  - `async react(room_id, event_id, emoji) -> None`
  - `async member_display_name(room_id, user_id) -> str | None`
  - module function `event_reply_target(source: dict) -> str`

- [ ] **Step 1: Write the failing test**

Create `tests/test_matrix_client.py`:

```python
"""Pure helpers of the nio wrapper. Network behaviour is exercised through the
bridge tests with a fake client."""
from bot.matrix.client import RoomEvent, event_reply_target, room_event_from_source


def test_reply_target_extracted():
    src = {"content": {"m.relates_to": {"m.in_reply_to": {"event_id": "$card"}}}}
    assert event_reply_target(src) == "$card"
    assert event_reply_target({"content": {}}) == ""
    assert event_reply_target({}) == ""


def test_room_event_from_source():
    src = {
        "room_id": "!r:x", "sender": "@a:x", "event_id": "$e", "origin_server_ts": 1234,
        "content": {"body": "hi", "m.relates_to": {"m.in_reply_to": {"event_id": "$card"}}},
    }
    ev = room_event_from_source(src)
    assert ev == RoomEvent(
        room_id="!r:x", sender="@a:x", event_id="$e", body="hi", reply_to="$card",
        server_ts=1234,
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: Docker test command with `pytest -q tests/test_matrix_client.py`.
Expected: FAIL — `ModuleNotFoundError: No module named 'bot.matrix.client'`.

- [ ] **Step 3: Implement the wrapper**

Create `bot/matrix/client.py`:

```python
"""Thin wrapper over matrix-nio. Knows Matrix; knows nothing about submissions.

Rooms are unencrypted by requirement, so nio is used without its e2e extra.
Events that predate ``started_ms`` are dropped by the caller (the initial sync
replays room history) and the bot's own events are never dispatched.
"""
from __future__ import annotations

import logging
import os
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from nio import (
    AsyncClient,
    AsyncClientConfig,
    RoomMessageMedia,
    RoomMessageText,
    SyncResponse,
)

from bot.config import MatrixSettings
from bot.matrix.render import html_body, plain_body

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RoomEvent:
    room_id: str
    sender: str
    event_id: str
    body: str
    reply_to: str
    server_ts: int


def event_reply_target(source: dict) -> str:
    relates = (source.get("content") or {}).get("m.relates_to") or {}
    return (relates.get("m.in_reply_to") or {}).get("event_id") or ""


def room_event_from_source(source: dict) -> RoomEvent:
    content = source.get("content") or {}
    return RoomEvent(
        room_id=source.get("room_id", ""),
        sender=source.get("sender", ""),
        event_id=source.get("event_id", ""),
        body=content.get("body") or "",
        reply_to=event_reply_target(source),
        server_ts=int(source.get("origin_server_ts") or 0),
    )


MessageHandler = Callable[[RoomEvent], Awaitable[None]]


class MatrixClient:
    def __init__(self, settings: MatrixSettings) -> None:
        self.settings = settings
        self.user_id = settings.user
        self.started_ms = 0
        self._client: AsyncClient | None = None
        self._handler: MessageHandler | None = None

    def on_message(self, handler: MessageHandler) -> None:
        self._handler = handler

    # --- lifecycle ---------------------------------------------------------

    async def connect(self) -> bool:
        os.makedirs(self.settings.store_dir, exist_ok=True)
        client = AsyncClient(
            self.settings.homeserver,
            self.settings.user,
            device_id=self.settings.device_name,
            store_path=self.settings.store_dir,
            config=AsyncClientConfig(request_timeout=30, max_timeout_retry_wait_time=30),
        )
        token = self.settings.token.get_secret_value()
        if token:
            client.access_token = token
            client.user_id = self.settings.user
            client.device_id = self.settings.device_name
        else:
            resp = await client.login(
                self.settings.password.get_secret_value(),
                device_name=self.settings.device_name,
            )
            if getattr(resp, "access_token", None) is None:
                logger.error("Matrix login failed: %s", type(resp).__name__)
                await client.close()
                return False
        whoami = await client.whoami()
        if not getattr(whoami, "user_id", None):
            logger.error("Matrix whoami failed: %s", type(whoami).__name__)
            await client.close()
            return False
        self.user_id = whoami.user_id
        self.started_ms = int(time.time() * 1000)
        client.add_event_callback(self._on_text, RoomMessageText)
        client.add_event_callback(self._on_text, RoomMessageMedia)
        client.add_response_callback(self._on_sync, SyncResponse)
        self._client = client
        logger.info("Matrix connected as %s", self.user_id)
        return True

    async def run_sync(self) -> None:
        assert self._client is not None
        await self._client.sync_forever(timeout=30000, full_state=False)

    async def close(self) -> None:
        if self._client is not None:
            await self._client.close()
            self._client = None

    # --- inbound -----------------------------------------------------------

    async def _on_sync(self, response) -> None:
        """Accept pending invites so operators only have to invite the bot."""
        assert self._client is not None
        for room_id in list(getattr(response.rooms, "invite", {}) or {}):
            result = await self._client.join(room_id)
            if getattr(result, "room_id", None):
                logger.info("Joined Matrix room %s", room_id)
                await self.send_html(room_id, f"Room id: <code>{room_id}</code>")
            else:
                logger.warning("Could not join Matrix room %s", room_id)

    async def _on_text(self, room, event) -> None:
        if self._handler is None or event.sender == self.user_id:
            return
        source = dict(event.source)
        source.setdefault("room_id", room.room_id)
        await self._handler(room_event_from_source(source))

    # --- outbound ----------------------------------------------------------

    async def send_html(self, room_id: str, html_text: str, *, reply_to: str | None = None) -> str:
        if self._client is None or not room_id:
            return ""
        content = {
            "msgtype": "m.text",
            "body": plain_body(html_text),
            "format": "org.matrix.custom.html",
            "formatted_body": html_body(html_text),
        }
        if reply_to:
            content["m.relates_to"] = {"m.in_reply_to": {"event_id": reply_to}}
        try:
            resp = await self._client.room_send(room_id, "m.room.message", content)
        except Exception:  # noqa: BLE001
            logger.exception("Matrix send failed")
            return ""
        return getattr(resp, "event_id", "") or ""

    async def edit_html(self, room_id: str, event_id: str, html_text: str) -> bool:
        if self._client is None or not room_id or not event_id:
            return False
        new_content = {
            "msgtype": "m.text",
            "body": plain_body(html_text),
            "format": "org.matrix.custom.html",
            "formatted_body": html_body(html_text),
        }
        content = dict(new_content)
        content["body"] = "* " + content["body"]
        content["m.new_content"] = new_content
        content["m.relates_to"] = {"rel_type": "m.replace", "event_id": event_id}
        try:
            await self._client.room_send(room_id, "m.room.message", content)
            return True
        except Exception:  # noqa: BLE001
            logger.exception("Matrix edit failed")
            return False

    async def upload_file(self, room_id: str, data: bytes, name: str, mime: str) -> str:
        if self._client is None or not room_id:
            return ""
        import io

        try:
            resp, _ = await self._client.upload(
                io.BytesIO(data), content_type=mime, filename=name, filesize=len(data)
            )
            uri = getattr(resp, "content_uri", "")
            if not uri:
                logger.warning("Matrix upload rejected: %s", type(resp).__name__)
                return ""
            msgtype = "m.image" if mime.startswith("image/") else "m.file"
            sent = await self._client.room_send(
                room_id, "m.room.message",
                {"msgtype": msgtype, "body": name, "url": uri,
                 "info": {"mimetype": mime, "size": len(data)}},
            )
        except Exception:  # noqa: BLE001
            logger.exception("Matrix upload failed")
            return ""
        return getattr(sent, "event_id", "") or ""

    async def react(self, room_id: str, event_id: str, emoji: str) -> None:
        if self._client is None:
            return
        try:
            await self._client.room_send(
                room_id, "m.reaction",
                {"m.relates_to": {"rel_type": "m.annotation", "event_id": event_id, "key": emoji}},
            )
        except Exception:  # noqa: BLE001
            logger.debug("Matrix reaction failed", exc_info=True)

    async def member_display_name(self, room_id: str, user_id: str) -> str | None:
        if self._client is None:
            return None
        room = self._client.rooms.get(room_id)
        if room is None:
            return None
        member = room.users.get(user_id)
        return getattr(member, "display_name", None) or None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: Docker test command with `pytest -q tests/test_matrix_client.py`.
Expected: PASS. Also run `ruff check bot` — fix any `ASYNC`/`B` findings.

- [ ] **Step 5: Commit**

```bash
git add bot/matrix/client.py tests/test_matrix_client.py
git commit -m "Add matrix-nio client wrapper"
```

---

### Task 7: The bridge — announce, refresh, and room actions

**Files:**
- Create: `bot/matrix/bridge.py`
- Test: `tests/test_matrix_bridge.py` (create)

**Interfaces:**
- Consumes: `MatrixClient` interface from Task 6 (faked in tests), `SubmissionActions` (Task 4), `UserRepository.get_or_create_matrix`, `MatrixDeliveryRepository` (Task 3), `render_room_card` (Task 5), `MatrixSettings.room_for/type_for_room` (Task 1).
- Produces: `MatrixBridge(client, settings, session_pool, bot, core, cipher, default_locale)` implementing `CardSink` (`announce(session, submission_id) -> bool`, `refresh(session, submission_id) -> None`) plus `handle_event(event: RoomEvent) -> None` and `run() -> None` (connect + sync with reconnect backoff).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_matrix_bridge.py`:

```python
"""The Matrix bridge: cards go to the right room, replies resolve to the
submission, members are provisioned as Users, and every action goes through
SubmissionActions (so type/ownership rules hold)."""
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from bot.config import MatrixSettings
from bot.db.base import Base
from bot.db.models import (
    AttachmentType,
    MatrixDelivery,
    Submission,
    SubmissionResponse,
    SubmissionStatus,
    SubmissionType,
    User,
)
from bot.matrix.bridge import MatrixBridge
from bot.matrix.client import RoomEvent
from bot.security.crypto import AnonCipher
from bot.services.submissions import AttachmentInput, SubmissionInput, SubmissionService

APPEAL_ROOM = "!appeal:x"
CORRUPTION_ROOM = "!corr:x"
NODIR = "@nodir:x"

_core = SimpleNamespace(get=lambda key, locale=None, **kw: f"{key}|{kw}" if kw else key)


class FakeClient:
    def __init__(self):
        self.user_id = "@anticorbot:x"
        self.started_ms = 0
        self.sent = []       # (room, html, reply_to)
        self.edits = []      # (room, event_id, html)
        self.uploads = []    # (room, name, mime, size)
        self.reactions = []
        self.names = {NODIR: "Nodir"}
        self._n = 0

    def _eid(self):
        self._n += 1
        return f"$e{self._n}"

    async def send_html(self, room_id, html_text, *, reply_to=None):
        self.sent.append((room_id, html_text, reply_to))
        return self._eid()

    async def edit_html(self, room_id, event_id, html_text):
        self.edits.append((room_id, event_id, html_text))
        return True

    async def upload_file(self, room_id, data, name, mime):
        self.uploads.append((room_id, name, mime, len(data)))
        return self._eid()

    async def react(self, room_id, event_id, emoji):
        self.reactions.append((event_id, emoji))

    async def member_display_name(self, room_id, user_id):
        return self.names.get(user_id)


@pytest_asyncio.fixture
async def env():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:", poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    pool = async_sessionmaker(engine, expire_on_commit=False)
    async with pool() as s:
        conn = await s.connection()
        await conn.run_sync(Base.metadata.create_all)

    settings = MatrixSettings(
        homeserver="https://m.x", user="@anticorbot:x", password="pw",
        room_appeal=APPEAL_ROOM, room_corruption=CORRUPTION_ROOM, locale="uz_latn",
    )
    cipher = AnonCipher(Fernet.generate_key().decode())
    bot = AsyncMock()
    bot.send_message.return_value = SimpleNamespace(message_id=1)
    bot.download.return_value = BytesIO(b"\xff\xd8jpegdata")
    client = FakeClient()
    bridge = MatrixBridge(client, settings, pool, bot, _core, cipher, "ru")
    yield SimpleNamespace(bridge=bridge, client=client, pool=pool, bot=bot, cipher=cipher)
    await engine.dispose()


async def _submission(env, *, type_=SubmissionType.appeal, anonymous=False,
                      author_tg=900, attachments=0):
    async with env.pool() as s:
        author_uid = None
        if not anonymous:
            author = User(tg_id=author_tg, language="ru")
            s.add(author)
            await s.flush()
            author_uid = author.id
        svc = SubmissionService(s, env.cipher)
        sub = await svc.create(SubmissionInput(
            type=type_, text="report <x>", is_anonymous=anonymous,
            author_tg_id=author_tg, author_user_id=author_uid,
            attachments=[AttachmentInput(f"file{i}", AttachmentType.photo) for i in range(attachments)],
        ))
        await s.commit()
        return sub.id


async def _announce(env, sub_id):
    async with env.pool() as s:
        ok = await env.bridge.announce(s, sub_id)
        await s.commit()
    return ok


async def _card_id(env, sub_id):
    async with env.pool() as s:
        row = await s.scalar(
            select(MatrixDelivery).where(
                MatrixDelivery.submission_id == sub_id, MatrixDelivery.kind == "card"
            )
        )
        return row.event_id


def _ev(room, body, reply_to="", sender=NODIR, ts=10_000, eid="$in"):
    return RoomEvent(room_id=room, sender=sender, event_id=eid, body=body,
                     reply_to=reply_to, server_ts=ts)


async def test_announce_posts_card_and_attachments_to_type_room(env):
    sub_id = await _submission(env, attachments=2)

    assert await _announce(env, sub_id) is True

    rooms = [room for room, _, _ in env.client.sent]
    assert rooms == [APPEAL_ROOM]
    assert "&lt;x&gt;" in env.client.sent[0][1]  # escaped user text
    assert [u[0] for u in env.client.uploads] == [APPEAL_ROOM, APPEAL_ROOM]
    async with env.pool() as s:
        kinds = sorted(r.kind for r in await s.scalars(select(MatrixDelivery)))
        assert kinds == ["attachment", "attachment", "card"]


async def test_corruption_goes_to_corruption_room(env):
    sub_id = await _submission(env, type_=SubmissionType.corruption, anonymous=True)
    await _announce(env, sub_id)
    assert env.client.sent[0][0] == CORRUPTION_ROOM
    assert "+998" not in env.client.sent[0][1]


async def test_announce_without_room_configured_returns_false(env):
    env.bridge.settings.room_appeal = ""
    sub_id = await _submission(env)
    assert await _announce(env, sub_id) is False
    assert env.client.sent == []


async def test_take_by_reply_provisions_user_and_claims(env):
    sub_id = await _submission(env)
    await _announce(env, sub_id)
    card = await _card_id(env, sub_id)

    await env.bridge.handle_event(_ev(APPEAL_ROOM, "!olish", reply_to=card))

    async with env.pool() as s:
        user = await s.scalar(select(User).where(User.matrix_id == NODIR))
        assert user is not None and user.resp_appeal and not user.is_admin
        assert user.full_name == "Nodir"
        sub = await s.get(Submission, sub_id)
        assert sub.status == SubmissionStatus.in_progress
        assert sub.assigned_to_user_id == user.id
    assert env.client.reactions[-1] == ("$in", "👍")
    # Card redrawn with the assignee.
    assert env.client.edits and "Nodir" in env.client.edits[-1][2]


async def test_reply_to_attachment_also_resolves(env):
    sub_id = await _submission(env, attachments=1)
    await _announce(env, sub_id)
    async with env.pool() as s:
        att = await s.scalar(select(MatrixDelivery).where(MatrixDelivery.kind == "attachment"))
    await env.bridge.handle_event(_ev(APPEAL_ROOM, "!olish", reply_to=att.event_id))
    async with env.pool() as s:
        assert (await s.get(Submission, sub_id)).status == SubmissionStatus.in_progress


async def test_text_reply_answers_applicant(env):
    sub_id = await _submission(env, author_tg=900)
    await _announce(env, sub_id)
    card = await _card_id(env, sub_id)

    await env.bridge.handle_event(_ev(APPEAL_ROOM, "Salom, ko'rib chiqamiz", reply_to=card))

    async with env.pool() as s:
        resp = (await s.scalars(select(SubmissionResponse))).one()
        assert resp.text == "Salom, ko'rib chiqamiz"
    assert any(c.args[0] == 900 for c in env.bot.send_message.await_args_list)
    assert env.client.reactions[-1][1] == "👍"


async def test_close_by_owner_notifies_and_redraws(env):
    sub_id = await _submission(env, author_tg=900)
    await _announce(env, sub_id)
    card = await _card_id(env, sub_id)
    await env.bridge.handle_event(_ev(APPEAL_ROOM, "!olish", reply_to=card))

    await env.bridge.handle_event(_ev(APPEAL_ROOM, "!yopish", reply_to=card))

    async with env.pool() as s:
        assert (await s.get(Submission, sub_id)).status == SubmissionStatus.closed
    assert any(c.args[0] == 900 for c in env.bot.send_message.await_args_list)
    assert "!karta" in env.client.edits[-1][2]


async def test_non_owner_cannot_close(env):
    sub_id = await _submission(env)
    await _announce(env, sub_id)
    card = await _card_id(env, sub_id)
    await env.bridge.handle_event(_ev(APPEAL_ROOM, "!olish", reply_to=card, sender=NODIR))

    await env.bridge.handle_event(_ev(APPEAL_ROOM, "!yopish", reply_to=card, sender="@other:x"))

    async with env.pool() as s:
        assert (await s.get(Submission, sub_id)).status == SubmissionStatus.in_progress
    assert "mx-not-owner" in env.client.sent[-1][1]


async def test_wrong_room_type_is_rejected(env):
    """A corruption card somehow answered from the appeal room: the user gets
    resp_appeal from the room, but the submission's type is re-derived."""
    sub_id = await _submission(env, type_=SubmissionType.corruption, anonymous=True)
    await _announce(env, sub_id)
    card = await _card_id(env, sub_id)

    await env.bridge.handle_event(_ev(APPEAL_ROOM, "!olish", reply_to=card))

    async with env.pool() as s:
        assert (await s.get(Submission, sub_id)).status == SubmissionStatus.new
    assert "mx-forbidden" in env.client.sent[-1][1]


async def test_chatter_and_old_and_own_events_are_ignored(env):
    sub_id = await _submission(env)
    await _announce(env, sub_id)
    card = await _card_id(env, sub_id)
    before = (len(env.client.sent), len(env.client.reactions))

    await env.bridge.handle_event(_ev(APPEAL_ROOM, "tushlikka?", reply_to=""))
    await env.bridge.handle_event(_ev(APPEAL_ROOM, "!olish", reply_to="$notours"))
    env.bridge.client.started_ms = 99_999
    await env.bridge.handle_event(_ev(APPEAL_ROOM, "!olish", reply_to=card, ts=5))
    env.bridge.client.started_ms = 0
    await env.bridge.handle_event(_ev(APPEAL_ROOM, "!olish", reply_to=card, sender="@anticorbot:x"))
    await env.bridge.handle_event(_ev("!unknown:x", "!olish", reply_to=card))

    assert (len(env.client.sent), len(env.client.reactions)) == before
    async with env.pool() as s:
        assert (await s.get(Submission, sub_id)).status == SubmissionStatus.new


async def test_help_and_card_commands(env):
    sub_id = await _submission(env)
    await _announce(env, sub_id)
    card = await _card_id(env, sub_id)

    await env.bridge.handle_event(_ev(APPEAL_ROOM, "!yordam"))
    assert "mx-help" in env.client.sent[-1][1]

    await env.bridge.handle_event(_ev(APPEAL_ROOM, "!karta", reply_to=card))
    assert "!olish" in env.client.sent[-1][1]


async def test_refresh_from_telegram_side_edits_card(env):
    sub_id = await _submission(env)
    await _announce(env, sub_id)
    async with env.pool() as s:
        sub = await s.get(Submission, sub_id)
        sub.status = SubmissionStatus.closed
        await s.commit()
        await env.bridge.refresh(s, sub_id)
    assert env.client.edits and "!karta" in env.client.edits[-1][2]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: Docker test command with `pytest -q tests/test_matrix_bridge.py`.
Expected: FAIL — `ModuleNotFoundError: No module named 'bot.matrix.bridge'`.

- [ ] **Step 3: Implement the bridge**

Create `bot/matrix/bridge.py`:

```python
"""Matrix bridge: submissions -> room cards, room replies -> SubmissionActions.

Every room event is handled in its own DB session (commit on success, rollback
on error) — the middleware chain does not run for Matrix events. The member
who acts is provisioned as a User on first sight with the role of the room
the message came from; from there on the rules are exactly the Telegram ones:
``SubmissionActions.authorize`` derives the type from the submission row, so
a card answered from the wrong room never grants a foreign type.
"""
from __future__ import annotations

import asyncio
import logging

from aiogram import Bot
from aiogram_i18n.cores import BaseCore
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from bot.config import MatrixSettings
from bot.db.models import AttachmentType, Submission, SubmissionType, User
from bot.db.repositories import MatrixDeliveryRepository, UserRepository
from bot.matrix.client import RoomEvent
from bot.matrix.render import parse_command, render_room_card, strip_reply_fallback
from bot.security.crypto import AnonCipher
from bot.services.actions import SubmissionActions, display_name
from bot.utils.text import escape

logger = logging.getLogger(__name__)

# Telegram Bot API refuses to download files larger than this.
MAX_DOWNLOAD = 20 * 1024 * 1024
_RECONNECT_MIN, _RECONNECT_MAX = 5, 120


class MatrixBridge:
    def __init__(
        self,
        client,
        settings: MatrixSettings,
        session_pool: async_sessionmaker,
        bot: Bot,
        core: BaseCore,
        cipher: AnonCipher,
        default_locale: str,
    ) -> None:
        self.client = client
        self.settings = settings
        self.pool = session_pool
        self.bot = bot
        self.core = core
        self.cipher = cipher
        self.default_locale = default_locale

    # --- lifecycle ---------------------------------------------------------

    async def run(self) -> None:
        """Connect and sync forever; reconnect with backoff. Never raises —
        Telegram must keep working whatever happens to Matrix."""
        self.client.on_message(self.handle_event)
        delay = _RECONNECT_MIN
        while True:
            try:
                if await self.client.connect():
                    delay = _RECONNECT_MIN
                    await self.client.run_sync()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                logger.exception("Matrix bridge stopped; reconnecting in %ss", delay)
            finally:
                await self.client.close()
            await asyncio.sleep(delay)
            delay = min(delay * 2, _RECONNECT_MAX)

    # --- CardSink ----------------------------------------------------------

    async def announce(self, session: AsyncSession, submission_id: int) -> bool:
        sub = await self._load(session, submission_id)
        if sub is None:
            return False
        room = self.settings.room_for(sub.type.value)
        if not room:
            return False
        deliveries = MatrixDeliveryRepository(session)

        failed = 0
        uploaded: list[str] = []
        for index, att in enumerate(sub.attachments):
            event_id = await self._upload_attachment(room, sub.public_id, index, att)
            if event_id:
                uploaded.append(event_id)
            else:
                failed += 1

        text = render_room_card(
            self.core, self.settings.locale, sub,
            assignee_name=await self._assignee_name(session, sub),
            attachment_count=len(sub.attachments), failed_attachments=failed,
        )
        card_id = await self.client.send_html(room, text)
        if not card_id:
            return False
        await deliveries.add(sub.id, room, card_id, "card")
        for event_id in uploaded:
            await deliveries.add(sub.id, room, event_id, "attachment")
        return True

    async def refresh(self, session: AsyncSession, submission_id: int) -> None:
        sub = await self._load(session, submission_id)
        if sub is None:
            return
        card = await MatrixDeliveryRepository(session).card_for(sub.id)
        if card is None:
            return
        text = render_room_card(
            self.core, self.settings.locale, sub,
            assignee_name=await self._assignee_name(session, sub),
            attachment_count=len(sub.attachments),
        )
        await self.client.edit_html(card.room_id, card.event_id, text)

    # --- inbound -----------------------------------------------------------

    async def handle_event(self, event: RoomEvent) -> None:
        if event.sender == self.client.user_id or event.server_ts < self.client.started_ms:
            return
        room_type = self.settings.type_for_room(event.room_id)
        if room_type is None:
            return

        body = strip_reply_fallback(event.body)
        command, args = parse_command(body)
        locale = self.settings.locale

        if command == "yordam":
            await self.client.send_html(event.room_id, self.core.get("mx-help", locale))
            return

        async with self.pool() as session:
            try:
                await self._handle(session, event, room_type, body, command, args)
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    async def _handle(
        self, session: AsyncSession, event: RoomEvent, room_type: str,
        body: str, command: str | None, args: str,
    ) -> None:
        sub_id = await self._resolve(session, event, command, args)
        if sub_id is None:
            return  # ordinary conversation between staff

        name = await self.client.member_display_name(event.room_id, event.sender)
        user, _ = await UserRepository(session).get_or_create_matrix(
            event.sender, name or event.sender.lstrip("@").split(":")[0],
            SubmissionType(room_type),
        )
        actions = SubmissionActions(
            session, self.bot, self.core, self.cipher, self.default_locale, sinks=[self],
        )
        locale = self.settings.locale
        room = event.room_id

        if command == "karta":
            sub = await actions.authorize(sub_id, user)
            if sub is None:
                await self._note(room, event, "mx-forbidden")
                return
            await self._repost(session, sub)
            return

        if command == "olish":
            sub = await actions.authorize(sub_id, user)
            if sub is None:
                await self._note(room, event, "mx-forbidden")
                return
            result = await actions.take(sub, user)
            if result.won:
                await self.client.react(room, event.event_id, "👍")
            else:
                await self._note(room, event, "cb-already-taken", name=result.assignee_name)
            return

        if command == "yopish":
            sub = await actions.authorize(sub_id, user, require_owner=True)
            if sub is None:
                await self._deny_owner(session, room, event, sub_id, user)
                return
            if await actions.close(sub, user):
                await self.client.react(room, event.event_id, "👍")
            else:
                await self._note(room, event, "mx-already-closed")
            return

        if command is not None:
            await self._note(room, event, "mx-help")
            return

        if not body:
            return
        sub = await actions.authorize(sub_id, user, require_owner=True)
        if sub is None:
            await self._deny_owner(session, room, event, sub_id, user)
            return
        delivered = await actions.reply(sub, user, body)
        if delivered:
            await self.client.react(room, event.event_id, "👍")
        else:
            await self._note(room, event, "mx-reply-undeliverable")

    # --- helpers -----------------------------------------------------------

    async def _resolve(
        self, session: AsyncSession, event: RoomEvent, command: str | None, args: str
    ) -> int | None:
        if event.reply_to:
            found = await MatrixDeliveryRepository(session).submission_id_for(
                event.room_id, event.reply_to
            )
            if found is not None:
                return found
        if command in {"olish", "yopish", "karta"} and args:
            return await session.scalar(
                select(Submission.id).where(Submission.public_id == args.upper())
            )
        return None

    async def _load(self, session: AsyncSession, submission_id: int) -> Submission | None:
        return await session.scalar(
            select(Submission)
            .options(selectinload(Submission.attachments))
            .where(Submission.id == submission_id)
            .execution_options(populate_existing=True)
        )

    async def _assignee_name(self, session: AsyncSession, sub: Submission) -> str | None:
        if sub.assigned_to_user_id is None:
            return None
        return display_name(await session.get(User, sub.assigned_to_user_id))

    async def _deny_owner(self, session, room, event, sub_id, user) -> None:
        sub = await session.get(Submission, sub_id)
        if sub is not None and sub.assigned_to_user_id not in (None, user.id):
            holder = display_name(await session.get(User, sub.assigned_to_user_id))
            await self._note(room, event, "mx-not-owner", name=holder)
        else:
            await self._note(room, event, "mx-forbidden")

    async def _note(self, room: str, event: RoomEvent, key: str, **kw) -> None:
        await self.client.send_html(
            room, self.core.get(key, self.settings.locale, **kw), reply_to=event.event_id
        )

    async def _repost(self, session: AsyncSession, sub: Submission) -> None:
        room = self.settings.room_for(sub.type.value)
        text = render_room_card(
            self.core, self.settings.locale, sub,
            assignee_name=await self._assignee_name(session, sub),
            attachment_count=len(sub.attachments),
        )
        event_id = await self.client.send_html(room, text)
        if event_id:
            await MatrixDeliveryRepository(session).add(sub.id, room, event_id, "card")

    async def _upload_attachment(self, room: str, public_id: str, index: int, att) -> str:
        """Pull the file from Telegram and push it into the room. '' on failure."""
        try:
            buffer = await self.bot.download(att.file_id)
            data = buffer.read() if hasattr(buffer, "read") else bytes(buffer)
        except Exception:  # noqa: BLE001 — too big, expired, network
            logger.warning("Attachment download failed for %s", public_id)
            return ""
        if not data or len(data) > MAX_DOWNLOAD:
            return ""
        if att.file_type == AttachmentType.photo:
            name, mime = f"{public_id}_{index + 1}.jpg", "image/jpeg"
        else:
            name, mime = f"{public_id}_{index + 1}", "application/octet-stream"
        return await self.client.upload_file(room, data, escape(name), mime)
```

Note on `_load`: `selectinload` is required — `Submission.attachments` is a lazy relationship and lazy loads raise under async SQLAlchemy.

- [ ] **Step 4: Run tests to verify they pass**

Run: Docker test command with `pytest -q tests/test_matrix_bridge.py`.
Expected: PASS. If `test_take_by_reply_provisions_user_and_claims` fails on the reaction assertion, check that `take()` won (a stale `Submission` instance from `_load` is fine — `try_claim` is a Core UPDATE).

- [ ] **Step 5: Run the full suite and ruff, then commit**

```bash
git add bot/matrix/bridge.py tests/test_matrix_bridge.py
git commit -m "Add the Matrix bridge: cards to rooms, actions by reply"
```

---

### Task 8: Wiring — new submissions, startup, import boundary

**Files:**
- Modify: `bot/handlers/submission.py:283-300` (`on_submit`: call sinks, count Matrix as delivered)
- Modify: `bot/__main__.py` (start the bridge, register it as a sink)
- Test: `tests/test_matrix_architecture.py` (create), `tests/test_handlers_flow.py` (append one test)

**Interfaces:**
- Consumes: `CardSink.announce(session, submission_id) -> bool`, `MatrixBridge`, `MatrixClient`, `create_session_pool`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_matrix_architecture.py`:

```python
"""Dependency direction: handlers and the Matrix bridge meet only in services."""
from pathlib import Path


def _sources(folder: str) -> dict[str, str]:
    return {p.name: p.read_text(encoding="utf-8") for p in Path(folder).glob("*.py")}


def test_matrix_package_never_imports_handlers():
    for name, src in _sources("bot/matrix").items():
        assert "bot.handlers" not in src, name


def test_handlers_never_import_matrix():
    for name, src in _sources("bot/handlers").items():
        assert "bot.matrix" not in src, name
```

Append to `tests/test_handlers_flow.py` (it has a `harness` fixture that drives the form through the dispatcher; look at `test_appeal_flow_keeps_author` for the sequence of updates and reuse its helpers):

```python
async def test_submit_announces_to_card_sinks(harness):
    """A configured sink receives the new submission and counts as a delivery
    even when there is no Telegram responsible."""
    announced = []

    class Sink:
        async def announce(self, session, submission_id):
            announced.append(submission_id)
            return True

        async def refresh(self, session, submission_id):
            pass

    harness.dp["card_sinks"] = [Sink()]
    await _run_appeal_flow(harness)  # the same helper test_appeal_flow_keeps_author uses

    assert len(announced) == 1
    # Applicant got the normal confirmation, not "no responsible".
    texts = [c.args[1] if len(c.args) > 1 else c.kwargs.get("text", "") for c in harness.bot.send_message.await_args_list]
    assert not any("no-responsible" in t for t in texts)
```

If `test_handlers_flow.py` has no shared helper for the full flow, extract the update sequence from `test_appeal_flow_keeps_author` into `async def _run_appeal_flow(harness)` and use it in both tests (small, mechanical refactor of the test file).

- [ ] **Step 2: Run tests to verify they fail**

Run: Docker test command with `pytest -q tests/test_matrix_architecture.py tests/test_handlers_flow.py`.
Expected: architecture tests PASS already (nothing to violate yet); the new flow test FAILS — `announced == []`.

- [ ] **Step 3: Call the sinks from `on_submit`**

In `bot/handlers/submission.py`, add `card_sinks: list | None = None` to the `on_submit` signature (after `settings`). Replace the block

```python
    delivered = await svc.dispatch_to_responsibles(
        query.bot, i18n.core, sub, default_locale=settings.default_locale
    )
    await session.commit()  # persist SubmissionDelivery rows
```

with

```python
    delivered = await svc.dispatch_to_responsibles(
        query.bot, i18n.core, sub, default_locale=settings.default_locale
    )
    await session.commit()  # persist SubmissionDelivery rows

    # Other channels (the Matrix rooms). A card there counts as a delivery:
    # on machines where Telegram is blocked it may be the only one.
    for sink in card_sinks or ():
        try:
            if await sink.announce(session, sub.id):
                delivered += 1
        except Exception:  # noqa: BLE001 — a sink must never break the submission
            logger.exception("card sink announce failed")
    await session.commit()
```

Make sure `logger = logging.getLogger(__name__)` exists at module top (add `import logging` if the module has no logger yet).

- [ ] **Step 4: Start the bridge in `__main__`**

Replace `bot/__main__.py`'s `main()` body from `bot, dp, redis, engine = build(settings)` down to the `finally` with:

```python
    bot, dp, redis, engine = build(settings)
    core = dp["i18n_core"]
    bridge_task: asyncio.Task | None = None
    bridge = None
    try:
        await core.startup()
        await set_commands(bot, core, settings.locales, settings.default_locale)

        if settings.matrix.enabled:
            from bot.db.session import create_session_pool
            from bot.matrix.bridge import MatrixBridge
            from bot.matrix.client import MatrixClient
            from bot.security.crypto import AnonCipher

            bridge = MatrixBridge(
                MatrixClient(settings.matrix), settings.matrix, create_session_pool(engine),
                bot, core, AnonCipher(settings.anon_enc_key), settings.default_locale,
            )
            dp["card_sinks"].append(bridge)
            bridge_task = asyncio.create_task(bridge.run(), name="matrix-bridge")
            logger.info("Matrix bridge enabled (locale=%s)", settings.matrix.locale)
        else:
            logger.info("Matrix bridge disabled (MATRIX__* not set)")

        if settings.use_webhook:
            await run_webhook(bot, dp, settings)
        else:
            await run_polling(bot, dp, settings)
    finally:
        if bridge_task is not None:
            bridge_task.cancel()
            try:
                await bridge_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        # Always release resources even if startup (set_commands etc.) fails.
        await bot.session.close()
        await redis.aclose()
        await engine.dispose()
```

- [ ] **Step 5: Run tests to verify they pass**

Run: Docker test command with `pytest -q tests/test_matrix_architecture.py tests/test_handlers_flow.py`.
Expected: PASS.

- [ ] **Step 6: Full suite + ruff, then commit**

Run the full Docker test command. Expected: ruff clean; everything passes except the 3 known concurrency errors.

```bash
git add bot/handlers/submission.py bot/__main__.py tests/test_matrix_architecture.py tests/test_handlers_flow.py
git commit -m "Announce new submissions to card sinks and start the Matrix bridge"
```

---

### Task 9: Postgres verification of the migration

**Files:** none changed (verification only; fix the migration if it fails)

- [ ] **Step 1: Start a throwaway Postgres**

```bash
docker network create anticor-test 2>/dev/null || true
docker run -d --rm --name anticor-pg --network anticor-test \
  -e POSTGRES_USER=anticor -e POSTGRES_PASSWORD=anticor -e POSTGRES_DB=anticor_test \
  postgres:16-alpine
sleep 8
```

- [ ] **Step 2: Run the suite with `TEST_PG_DSN` (this also runs the 3 concurrency tests)**

```bash
cd /c/Users/uge226/Desktop/anticor_bot
MSYS_NO_PATHCONV=1 docker run --rm --network anticor-test \
  -v "$(pwd -W):/app" -v "C:/php/extras/ssl/cacert.pem:/certs/cacert.pem:ro" \
  -e PIP_CERT=/certs/cacert.pem -e SSL_CERT_FILE=/certs/cacert.pem \
  -e TEST_PG_DSN=postgresql+asyncpg://anticor:anticor@anticor-pg:5432/anticor_test \
  -w /app python:3.12-slim \
  sh -c "pip install -q -e '.[dev]' 2>/dev/null; pytest -q"
```

Expected: **0 errors**, all passed.

- [ ] **Step 3: Apply the real migration to Postgres and check the schema**

```bash
MSYS_NO_PATHCONV=1 docker run --rm --network anticor-test \
  -v "$(pwd -W):/app" -v "C:/php/extras/ssl/cacert.pem:/certs/cacert.pem:ro" \
  -e PIP_CERT=/certs/cacert.pem -e SSL_CERT_FILE=/certs/cacert.pem \
  -w /app python:3.12-slim \
  sh -c "pip install -q -e '.[dev]' 2>/dev/null; python -c \"
from bot.db.migrate import upgrade_to_head
upgrade_to_head('postgresql+asyncpg://anticor:anticor@anticor-pg:5432/anticor_test')
print('upgrade ok')\""
docker exec anticor-pg psql -U anticor -d anticor_test -c "\d users" -c "\d matrix_deliveries"
```

Expected: `tg_id` shown without `not null`; `matrix_id` present with a unique constraint; `ck_users_identity` check listed; `matrix_deliveries` table with the unique `(room_id, event_id)`.

- [ ] **Step 4: Stop the database**

```bash
docker stop anticor-pg
```

If anything in Steps 2–3 failed, fix `alembic/versions/0002_matrix.py`, re-run, and commit the fix:

```bash
git add alembic/versions/0002_matrix.py
git commit -m "Fix Matrix migration on Postgres"
```

---

### Task 10: Documentation and the pull request

**Files:**
- Modify: `README.md` (new section, Russian)
- Modify: `CLAUDE.md` (architecture notes)

- [ ] **Step 1: README section**

In `README.md`, after the «Возможности» list, add a bullet:

```markdown
- 💬 **Мост в Matrix (Element)**: карточки заявок уходят в комнаты Element (по одной на тип), ответственные берут, отвечают и закрывают заявки прямо оттуда — для рабочих мест, где Telegram недоступен.
```

After the «Конфигурация (`.env`)» section add:

```markdown
## Matrix (Element) — необязательно

Для сотрудников, у которых на рабочем месте заблокирован Telegram. Каждая
новая заявка дублируется карточкой в комнату Element своего типа
(`MATRIX__ROOM_APPEAL` / `MATRIX__ROOM_CORRUPTION`); вложения загружаются
следом. Действия — **ответом (reply) на карточку**:

| Ответ на карточку | Что происходит |
|---|---|
| текст | ответ заявителю на его языке |
| `!olish` | взять в работу (атомарно, как в Telegram) |
| `!yopish` | закрыть, уведомить заявителя |
| `!karta` | показать карточку заново |
| `!yordam` | справка |

**Участник комнаты — сотрудник.** При первом действии бот создаёт для него
пользователя с ролью по типу комнаты (`resp_appeal` для комнаты обращений,
`resp_corruption` для комнаты жалоб). Владение («взял — твоё»), аудит и
атомарность — те же, что в Telegram; действие всегда записывается на
конкретного человека. **Состав комнат — это и есть выдача ролей**: кого
пригласили в комнату жалоб, тот их видит. Контролируйте его так же строго,
как `/assign`.

Настройка:

1. Создайте на homeserver аккаунт бота (например `@anticorbot:…`), две комнаты
   **без сквозного шифрования**, пригласите бота — он примет приглашение сам и
   напишет ID комнаты.
2. Заполните `MATRIX__HOMESERVER`, `MATRIX__USER`, `MATRIX__PASSWORD` (или
   `MATRIX__TOKEN`), `MATRIX__ROOM_APPEAL`, `MATRIX__ROOM_CORRUPTION`.
   Язык сообщений комнаты — `MATRIX__LOCALE` (по умолчанию `uz_latn`).
3. Примонтируйте volume на `MATRIX__STORE_DIR` (состояние синхронизации).

Если Matrix недоступен, бот работает как раньше — только Telegram. Пустые
`MATRIX__*` = мост выключен.
```

- [ ] **Step 2: CLAUDE.md notes**

In `CLAUDE.md`, in «## Architecture» after the «### Registry» subsection, add:

```markdown
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
  from the submission row — a card answered from the wrong room grants nothing.
- Cross-channel redraw goes through the `CardSink` protocol; handlers get
  `card_sinks` from dispatcher context and never import `bot.matrix`
  ([tests/test_matrix_architecture.py](tests/test_matrix_architecture.py)).
- Matrix events run outside the middleware chain: the bridge opens its own
  session per event (commit/rollback like `DbSessionMiddleware`).
- Rooms are unencrypted (no `[e2e]` extra, no libolm). Room strings are
  `mx-*` keys in all five `.ftl` files, rendered in `MATRIX__LOCALE`.
```

In the «## Project» paragraph's stack line, append `, matrix-nio (optional Element bridge)`.

- [ ] **Step 3: Full suite one last time, then commit and push the branch**

Run the full Docker test command. Expected: ruff clean, all green (3 concurrency errors only if Postgres is not provided).

```bash
git add README.md CLAUDE.md
git commit -m "Document the Matrix bridge"
git push -u origin feature/matrix-bridge
```

- [ ] **Step 4: Open the pull request**

`gh` is not installed on this machine; open the PR in the browser:
`https://github.com/uzgidro/anticor_bot/pull/new/feature/matrix-bridge`

PR title: `Matrix (Element) bridge for responsibles`

PR body:

```markdown
Responsibles work on machines where Telegram is blocked; the organisation runs
its own Synapse. This adds Element rooms (one per submission type) as a second
channel with the same rights, ownership and audit as the Telegram push cards.

Spec: docs/superpowers/specs/2026-09-11-matrix-bridge-design.md

- take/close/reply extracted into `SubmissionActions` — handlers and the bridge
  share one implementation (authorization stays in one place)
- room members are provisioned as Users (`matrix_id`, `tg_id` NULL) on first
  action, role from the room type; `responsibles_for()` excludes them from
  Telegram fan-out
- cards + attachments to the type's room; status changes redraw cards in all
  channels via `CardSink`
- migration 0002: nullable `users.tg_id`, `users.matrix_id`, `matrix_deliveries`
  (batch mode — runs on SQLite and Postgres)
- bridge disabled unless `MATRIX__*` is set → no behaviour change for the
  current deployment until configured
- new `mx-*` strings in all five locales

Deploy notes: create the bot account and two unencrypted rooms, invite the bot,
set `MATRIX__*`, mount a volume for `MATRIX__STORE_DIR`. Room membership now
grants `resp_<type>` — restrict it accordingly.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
```

Merging into `main` deploys to production through the existing pipeline — leave that decision to the maintainer.

---

## Self-review against the spec

- **Membership = User with room role** → Task 3 (`get_or_create_matrix`), Task 7 (`_handle`).
- **Two rooms by type; type re-derived from the row** → Task 1 (`room_for`/`type_for_room`), Task 7 (`authorize` from `SubmissionActions`; `test_wrong_room_type_is_rejected`).
- **Cards + attachments; 20 MB; failure noted on card** → Task 7 (`announce`, `_upload_attachment`, `failed_attachments`).
- **Actions by reply; `!olish/!yopish/!karta/!yordam`; also `!olish <public_id>`** → Task 7 (`_resolve`, `_handle`).
- **Status changes redraw everywhere** → Task 4 (`_refresh` via sinks), Task 7 (`refresh`), Task 8 (sinks wired).
- **Full card redraw in the room vs status-only in Telegram** → Task 5 (`render_room_card`), unchanged `update_all_cards`.
- **`tg_id` nullable + CHECK; `responsibles_for` filter; admin listing** → Tasks 2–3.
- **Own session per event; commit/rollback** → Task 7 (`handle_event`).
- **Old and own events ignored** → Task 6 (`started_ms`, sender check), Task 7 (`test_chatter_and_old_and_own_events_are_ignored`).
- **Bridge failures never break Telegram** → Task 7 (`run` loop), Task 8 (`try/except` around `announce`), Task 4 (`_refresh` swallows sink errors).
- **Locale of the room / author locale unchanged** → Task 5, Task 4 (`author_locale`).
- **Config, enabled flag, secrets** → Task 1.
- **Import boundary test** → Task 8.
- **Migration reversible with a guard** → Task 2 (`downgrade`).
- **Deploy notes, README, CLAUDE.md, PR** → Task 10.
- **Postgres run of the concurrency tests + migration** → Task 9.

Type consistency checked: `CardSink.announce(session, submission_id) -> bool` and `refresh(session, submission_id)` are used with those signatures in Tasks 4, 7, 8; `TakeResult(won, assignee_name)` in Tasks 4 and 7; `MatrixDeliveryRepository.add/submission_id_for/card_for` in Tasks 3 and 7; `RoomEvent` fields in Tasks 6 and 7; `MatrixSettings.room_for/type_for_room` in Tasks 1 and 7.
