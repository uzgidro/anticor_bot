"""After acting from the registry, the detail screen must reflect the new state.

A click from a push card must NOT be redrawn this way — that path is handled by
update_all_cards, and redrawing it would blank the card for other responsibles.
"""
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from bot.db.base import Base
from bot.db.models import Submission, SubmissionStatus, SubmissionType
from bot.handlers import registry
from bot.keyboards.inline import reaction_keyboard, registry_detail_kb


class _I18n:
    def get(self, key, /, *args, **kwargs):
        if not kwargs:
            return key
        return f"{key} " + " ".join(str(v) for v in kwargs.values())


class _Msg:
    def __init__(self, markup):
        self.reply_markup = markup
        self.edited = []

    async def edit_text(self, text, reply_markup=None):
        self.edited.append((text, reply_markup))


class _Query:
    def __init__(self, markup):
        self.message = _Msg(markup)

    async def answer(self, text=None, show_alert=False):
        pass


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


async def _sub(session):
    sub = Submission(
        type=SubmissionType.appeal, text="body", is_anonymous=False,
        public_id="PID001", ticket_number="TKT-2026-0001",
        status=SubmissionStatus.new,
    )
    session.add(sub)
    await session.flush()
    return sub


@pytest.mark.asyncio
async def test_refresh_redraws_when_click_came_from_registry(db):
    sub = await _sub(db)
    markup = registry_detail_kb(
        _I18n(), submission_id=sub.id, type_="appeal", status="all",
        order="desc", page=0, status_value="new",
    )
    query = _Query(markup)
    did = await registry.refresh_detail(query, _I18n(), db, sub.id)
    assert did is True
    assert query.message.edited


@pytest.mark.asyncio
async def test_refresh_is_noop_for_push_card_click(db):
    """A push card carries only ReactionCb — no registry state to return to."""
    sub = await _sub(db)
    query = _Query(reaction_keyboard(_I18n(), sub.id))
    did = await registry.refresh_detail(query, _I18n(), db, sub.id)
    assert did is False
    assert query.message.edited == []


@pytest.mark.asyncio
async def test_refresh_is_noop_when_message_has_no_keyboard(db):
    sub = await _sub(db)
    query = _Query(None)
    assert await registry.refresh_detail(query, _I18n(), db, sub.id) is False


@pytest.mark.asyncio
async def test_refreshed_keyboard_drops_take_after_claim(db):
    sub = await _sub(db)
    markup = registry_detail_kb(
        _I18n(), submission_id=sub.id, type_="appeal", status="all",
        order="desc", page=0, status_value="new",
    )
    query = _Query(markup)
    sub.status = SubmissionStatus.in_progress
    await db.flush()

    await registry.refresh_detail(query, _I18n(), db, sub.id)
    _, new_markup = query.message.edited[-1]
    texts = [b.text for row in new_markup.inline_keyboard for b in row]
    assert "btn-take" not in texts
    assert "btn-back-to-list" in texts


@pytest.mark.asyncio
async def test_refresh_preserves_filter_and_page_for_back_button(db):
    sub = await _sub(db)
    markup = registry_detail_kb(
        _I18n(), submission_id=sub.id, type_="appeal", status="new",
        order="asc", page=3, status_value="new",
    )
    query = _Query(markup)
    await registry.refresh_detail(query, _I18n(), db, sub.id)

    from bot.keyboards.inline import RegistryCb

    _, new_markup = query.message.edited[-1]
    back = [
        RegistryCb.unpack(b.callback_data)
        for row in new_markup.inline_keyboard
        for b in row
        if b.callback_data and b.callback_data.startswith("reg:")
    ][0]
    assert (back.status, back.order, back.page) == ("new", "asc", 3)


@pytest.mark.asyncio
async def test_refresh_is_noop_when_submission_vanished(db):
    sub = await _sub(db)
    markup = registry_detail_kb(
        _I18n(), submission_id=sub.id, type_="appeal", status="all",
        order="desc", page=0, status_value="new",
    )
    query = _Query(markup)
    assert await registry.refresh_detail(query, _I18n(), db, 999999) is False


def test_take_and_close_handlers_call_refresh():
    """The wiring itself: without these calls the registry screen goes stale.

    Asserted against the source because the handlers' own paths need a full
    dispatcher; the end-to-end behaviour is covered in test_registry_flow.
    """
    import inspect

    from bot.handlers import responsible

    for handler in (responsible.on_take, responsible.on_close):
        src = inspect.getsource(handler)
        assert "refresh_detail" in src, f"{handler.__name__} never refreshes the registry"


def test_registry_does_not_import_responsible():
    """One-way dependency: the reverse would be a circular import at startup."""
    import inspect

    from bot.handlers import registry as registry_module

    src = inspect.getsource(registry_module)
    assert "import responsible" not in src
    assert "from bot.handlers.responsible" not in src
