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
