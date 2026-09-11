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
        text = render.render_room_card(
            core, locale, _sub(), assignee_name=None, attachment_count=1
        )
        assert "ABCDEFGH" in text
