"""Registry access control.

The registry must be a new ENTRY POINT to existing rights, never new rights.
The critical assertion: a user responsible only for appeals can never reach a
corruption row, no matter what callback data they craft by hand.
"""
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from bot.db.base import Base
from bot.db.models import Submission, SubmissionStatus, SubmissionType, User
from bot.handlers import registry
from bot.keyboards.inline import RegistryCb


class _I18n:
    """Stub i18n that renders params too.

    Returning the bare key would silently swallow interpolated values, so a
    test asserting a public_id or the anonymity glyph reaches the screen would
    pass no matter what the handler did.
    """

    def get(self, key, /, *args, **kwargs):
        if not kwargs:
            return key
        params = " ".join(str(v) for v in kwargs.values())
        return f"{key} {params}"


class _Query:
    """Minimal CallbackQuery stand-in capturing what the handler produced."""

    def __init__(self):
        self.answers = []
        self.edits = []
        self.message = self
        self.reply_markup = None
        self.bot = None

    async def answer(self, text=None, show_alert=False):
        self.answers.append((text, show_alert))

    async def edit_text(self, text, reply_markup=None):
        self.edits.append((text, reply_markup))


@pytest_asyncio.fixture
async def db():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:", poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    pool = async_sessionmaker(engine, expire_on_commit=False)
    async with pool() as s:
        conn = await s.connection()
        await conn.run_sync(Base.metadata.create_all)
        yield s
    await engine.dispose()


async def _make(session, type_, public_id="PID001"):
    sub = Submission(
        type=type_, text="secret body", is_anonymous=False,
        public_id=public_id, ticket_number="TKT-2026-0001",
        status=SubmissionStatus.new,
    )
    session.add(sub)
    await session.flush()
    return sub


@pytest.mark.asyncio
async def test_appeal_responsible_cannot_list_corruption(db):
    """Hand-crafted callback data asking for the other type must be refused."""
    user = User(tg_id=1, resp_appeal=True, resp_corruption=False)
    db.add(user)
    await db.flush()
    await _make(db, SubmissionType.corruption)

    query = _Query()
    cb = RegistryCb(type="corruption", status="all", order="desc", page=0, open="")
    await registry.on_registry_nav(query, cb, _I18n(), user, db)

    assert query.edits == []                       # nothing rendered
    assert query.answers and query.answers[0][1]   # alert shown


@pytest.mark.asyncio
async def test_appeal_responsible_cannot_open_corruption_detail(db):
    user = User(tg_id=1, resp_appeal=True, resp_corruption=False)
    db.add(user)
    await db.flush()
    await _make(db, SubmissionType.corruption, public_id="CORR01")

    query = _Query()
    cb = RegistryCb(type="corruption", status="all", order="desc", page=0, open="CORR01")
    await registry.on_registry_nav(query, cb, _I18n(), user, db)

    rendered = " ".join(t for t, _ in query.edits)
    assert "secret body" not in rendered
    assert query.answers and query.answers[0][1]


@pytest.mark.asyncio
async def test_crafted_type_cannot_smuggle_foreign_submission(db):
    """Claiming type=appeal while opening a corruption public_id must fail.

    The row's own type is re-derived server-side, so the type gate cannot be
    bypassed by lying about it in the payload.
    """
    user = User(tg_id=1, resp_appeal=True, resp_corruption=False)
    db.add(user)
    await db.flush()
    await _make(db, SubmissionType.corruption, public_id="CORR01")

    query = _Query()
    cb = RegistryCb(type="appeal", status="all", order="desc", page=0, open="CORR01")
    await registry.on_registry_nav(query, cb, _I18n(), user, db)

    rendered = " ".join(t for t, _ in query.edits)
    assert "secret body" not in rendered
    assert any("registry-not-found" in str(a[0]) for a in query.answers)


@pytest.mark.asyncio
async def test_plain_citizen_is_refused(db):
    user = User(tg_id=2, resp_appeal=False, resp_corruption=False, is_admin=False)
    db.add(user)
    await db.flush()
    await _make(db, SubmissionType.appeal)

    query = _Query()
    cb = RegistryCb(type="appeal", status="all", order="desc", page=0, open="")
    await registry.on_registry_nav(query, cb, _I18n(), user, db)

    assert query.edits == []
    assert query.answers and query.answers[0][1]


@pytest.mark.asyncio
async def test_admin_sees_both_types(db):
    user = User(tg_id=3, is_admin=True)
    db.add(user)
    await db.flush()
    await _make(db, SubmissionType.corruption, public_id="CORR01")

    query = _Query()
    cb = RegistryCb(type="corruption", status="all", order="desc", page=0, open="")
    await registry.on_registry_nav(query, cb, _I18n(), user, db)

    assert query.edits  # rendered something


@pytest.mark.asyncio
async def test_responsible_sees_own_type(db):
    user = User(tg_id=4, resp_corruption=True)
    db.add(user)
    await db.flush()
    await _make(db, SubmissionType.corruption, public_id="CORR01")

    query = _Query()
    cb = RegistryCb(type="corruption", status="all", order="desc", page=0, open="")
    await registry.on_registry_nav(query, cb, _I18n(), user, db)

    assert query.edits
    assert "CORR01" in query.edits[0][0]


@pytest.mark.asyncio
async def test_anonymous_row_shows_no_author_in_list(db):
    user = User(tg_id=5, resp_corruption=True)
    db.add(user)
    sub = Submission(
        type=SubmissionType.corruption, text="body", is_anonymous=True,
        public_id="ANON01", ticket_number="TKT-2026-0002",
        status=SubmissionStatus.new, full_name=None, phone=None,
    )
    db.add(sub)
    await db.flush()

    query = _Query()
    cb = RegistryCb(type="corruption", status="all", order="desc", page=0, open="")
    await registry.on_registry_nav(query, cb, _I18n(), user, db)

    text = query.edits[0][0]
    assert "🕵" in text


@pytest.mark.asyncio
async def test_anonymous_detail_shows_no_pii(db):
    """Even if a stale row still carried a name, the anon branch must win."""
    user = User(tg_id=8, resp_corruption=True)
    db.add(user)
    sub = Submission(
        type=SubmissionType.corruption, text="body", is_anonymous=True,
        public_id="ANON02", ticket_number="TKT-2026-0003",
        status=SubmissionStatus.new, full_name="Should Not Appear",
        phone="+998000000000",
    )
    db.add(sub)
    await db.flush()

    query = _Query()
    cb = RegistryCb(type="corruption", status="all", order="desc", page=0, open="ANON02")
    await registry.on_registry_nav(query, cb, _I18n(), user, db)

    text = query.edits[0][0]
    assert "Should Not Appear" not in text
    assert "+998000000000" not in text


@pytest.mark.asyncio
async def test_missing_submission_reports_not_found(db):
    user = User(tg_id=6, resp_appeal=True)
    db.add(user)
    await db.flush()

    query = _Query()
    cb = RegistryCb(type="appeal", status="all", order="desc", page=0, open="NOPE99")
    await registry.on_registry_nav(query, cb, _I18n(), user, db)

    assert any("registry-not-found" in str(a[0]) for a in query.answers)


@pytest.mark.asyncio
async def test_empty_registry_renders_empty_notice(db):
    user = User(tg_id=7, resp_appeal=True)
    db.add(user)
    await db.flush()

    query = _Query()
    cb = RegistryCb(type="appeal", status="all", order="desc", page=0, open="")
    await registry.on_registry_nav(query, cb, _I18n(), user, db)

    assert query.edits[0][0] == "registry-empty"
