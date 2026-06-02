"""Tests for role filters, object-level authz, keyboards/CallbackData, text utils."""
from types import SimpleNamespace

import pytest

from bot.db.models import SubmissionType, User

# ---------- filters ----------

@pytest.mark.asyncio
async def test_is_admin_filter():
    from bot.filters.roles import IsAdmin

    f = IsAdmin()
    assert await f(SimpleNamespace(), db_user=User(tg_id=1, is_admin=True)) is True
    assert await f(SimpleNamespace(), db_user=User(tg_id=2, is_admin=False)) is False
    assert await f(SimpleNamespace(), db_user=None) is False


@pytest.mark.asyncio
async def test_is_responsible_by_type():
    from bot.filters.roles import IsResponsible

    appeal_user = User(tg_id=1, resp_appeal=True, resp_corruption=False)
    corr_user = User(tg_id=2, resp_appeal=False, resp_corruption=True)

    assert await IsResponsible(SubmissionType.appeal)(None, db_user=appeal_user) is True
    assert await IsResponsible(SubmissionType.appeal)(None, db_user=corr_user) is False
    assert await IsResponsible(SubmissionType.corruption)(None, db_user=corr_user) is True
    # No type -> any responsibility.
    assert await IsResponsible()(None, db_user=appeal_user) is True
    assert await IsResponsible()(None, db_user=User(tg_id=3)) is False


def test_can_handle_type_object_level():
    from bot.filters.roles import can_handle_type

    appeal_user = User(tg_id=1, resp_appeal=True)
    admin = User(tg_id=2, is_admin=True)
    assert can_handle_type(appeal_user, SubmissionType.appeal) is True
    assert can_handle_type(appeal_user, SubmissionType.corruption) is False
    assert can_handle_type(admin, SubmissionType.corruption) is True  # admin override


# ---------- CallbackData roundtrip ----------

def test_callback_data_pack_unpack():
    from bot.keyboards.inline import LangCb, ReactionCb

    packed = ReactionCb(action="take", submission_id=42).pack()
    assert ReactionCb.unpack(packed).submission_id == 42
    assert len(packed.encode()) <= 64  # Telegram callback_data limit

    assert LangCb.unpack(LangCb(code="uz_latn").pack()).code == "uz_latn"


def test_language_keyboard_has_five():
    from bot.keyboards.inline import LANGUAGES, language_keyboard

    kb = language_keyboard()
    buttons = [b for row in kb.inline_keyboard for b in row]
    assert len(buttons) == len(LANGUAGES) == 5


# ---------- text utils ----------

def test_split_text_under_limit():
    from bot.utils.text import split_text

    assert split_text("short") == ["short"]


def test_split_text_over_limit():
    from bot.utils.text import split_text

    text = "a " * 3000  # ~6000 chars
    parts = split_text(text, limit=4096)
    assert len(parts) >= 2
    assert all(len(p) <= 4096 for p in parts)


def test_escape_prevents_html_injection():
    from bot.utils.text import escape

    assert escape("<b>x</b>") == "&lt;b&gt;x&lt;/b&gt;"
