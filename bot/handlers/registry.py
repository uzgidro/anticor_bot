"""Submissions registry: a browsable list for responsibles and admins.

This is a new ENTRY POINT to existing rights, not new rights. Visibility is
decided by ``can_handle_type`` — the same check that gates the push card — and
actions reuse ReactionCb handled by responsible.py, so authorization lives in
exactly one place.

Filter/sort/page state travels inside the callback payload rather than FSM, so
the registry never collides with the submission form's state and an old
keyboard still works after a restart.
"""
from __future__ import annotations

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message
from aiogram_i18n import I18nContext
from sqlalchemy import select

from bot.db.models import Submission, SubmissionStatus, SubmissionType, User
from bot.db.repositories import SubmissionRepository
from bot.filters.roles import can_handle_type
from bot.keyboards.inline import (
    MenuCb,
    RegistryCb,
    registry_detail_kb,
    registry_list_kb,
)
from bot.utils.text import escape, split_text

router = Router(name="registry")

PER_PAGE = 5


def _status_filter(value: str) -> SubmissionStatus | None:
    """'all' means no status filter; anything else maps to the enum."""
    if value == "all":
        return None
    try:
        return SubmissionStatus(value)
    except ValueError:
        return None


def _author_label(sub: Submission) -> str:
    """Author cell: never reveal an anonymous complainant.

    Mirrors the push card exactly — anonymous rows show the spy glyph, and a
    non-anonymous row with no name shows a dash rather than leaking a username
    or tg_id into a list that may be read over someone's shoulder.
    """
    if sub.is_anonymous:
        return "🕵"
    return escape(sub.full_name) if sub.full_name else "—"


def _render_list(
    i18n, type_: SubmissionType, status: str, order: str, rows: list[Submission]
) -> str:
    type_label = i18n.get(f"type-{type_.value}")
    filter_label = i18n.get(
        "btn-filter-all" if status == "all" else f"btn-filter-{status.replace('_', '-')}"
    )
    order_label = i18n.get("btn-sort-newest" if order == "desc" else "btn-sort-oldest")
    lines = [
        i18n.get("registry-title", type=type_label, filter=filter_label, order=order_label),
        "",
    ]
    for n, sub in enumerate(rows, start=1):
        lines.append(
            i18n.get(
                "registry-item",
                n=n,
                status=i18n.get(f"status-{sub.status.value}"),
                public_id=sub.public_id,
                author=_author_label(sub),
                date=sub.created_at.strftime("%d.%m.%Y"),
            )
        )
    return "\n".join(lines)


def _render_detail(i18n, sub: Submission) -> str:
    """Detail view, identical in content to the push card."""
    lines = [i18n.get("card-title", public_id=sub.public_id)]
    lines.append(i18n.get("card-type", type=i18n.get(f"type-{sub.type.value}")))
    if sub.is_anonymous:
        # The anonymous branch wins unconditionally: never fall through to the
        # name/phone fields even if a legacy row still carries them.
        lines.append(i18n.get("card-anonymous"))
    else:
        if sub.full_name:
            lines.append(i18n.get("card-from", name=escape(sub.full_name)))
        if sub.phone:
            lines.append(i18n.get("card-phone", phone=escape(sub.phone)))
    lines.append(i18n.get("card-text", text=escape(sub.text)))
    lines.append(i18n.get("card-status", status=i18n.get(f"status-{sub.status.value}")))
    return "\n".join(lines)


async def _edit(query: CallbackQuery, text: str, markup) -> None:
    """Edit in place, tolerating Telegram's 'not modified' on a no-op tap."""
    try:
        await query.message.edit_text(text, reply_markup=markup)
    except TelegramBadRequest:
        pass


@router.callback_query(RegistryCb.filter())
async def on_registry_nav(
    query: CallbackQuery, callback_data: RegistryCb, i18n: I18nContext,
    db_user: User, session,
) -> None:
    """Single entry for every registry click: filter, page, open, back."""
    try:
        type_ = SubmissionType(callback_data.type)
    except ValueError:
        await query.answer(i18n.get("error-generic"), show_alert=True)
        return

    # Re-checked on EVERY click: the keyboard may outlive a role change, and
    # callback data is client-supplied.
    if not can_handle_type(db_user, type_):
        await query.answer(i18n.get("admin-only"), show_alert=True)
        return

    if callback_data.open:
        await _show_detail(query, callback_data, i18n, type_, session)
        return

    await _show_list(query, callback_data, i18n, type_, session)


async def _show_list(query, cb: RegistryCb, i18n, type_: SubmissionType, session) -> None:
    repo = SubmissionRepository(session)
    page = max(cb.page, 0)
    rows, total = await repo.list_for_registry(
        type_=type_, status=_status_filter(cb.status), order=cb.order,
        page=page, per_page=PER_PAGE,
    )
    if not rows and total and page > 0:
        # The filter changed under an out-of-range page: fall back to the first.
        page = 0
        rows, total = await repo.list_for_registry(
            type_=type_, status=_status_filter(cb.status), order=cb.order,
            page=0, per_page=PER_PAGE,
        )

    total_pages = max((total + PER_PAGE - 1) // PER_PAGE, 1)
    text = (
        _render_list(i18n, type_, cb.status, cb.order, rows)
        if rows else i18n.get("registry-empty")
    )
    markup = registry_list_kb(
        i18n, type_=cb.type, status=cb.status, order=cb.order, page=page,
        public_ids=[r.public_id for r in rows], total_pages=total_pages,
    )
    await _edit(query, text, markup)
    await query.answer()


async def _show_detail(query, cb: RegistryCb, i18n, type_: SubmissionType, session) -> None:
    sub = await session.scalar(select(Submission).where(Submission.public_id == cb.open))
    # Type is re-derived from the ROW, not from callback data: a crafted payload
    # must not smuggle a foreign-type submission past the check above.
    if sub is None or sub.type != type_:
        await query.answer(i18n.get("registry-not-found"), show_alert=True)
        return

    markup = registry_detail_kb(
        i18n, submission_id=sub.id, type_=cb.type, status=cb.status,
        order=cb.order, page=cb.page, status_value=sub.status.value,
    )
    await _edit(query, split_text(_render_detail(i18n, sub))[0], markup)
    await query.answer()


async def open_registry(
    message: Message, i18n: I18nContext, db_user: User, session,
    type_: SubmissionType,
) -> None:
    """Entry point from the main menu / a command: send the first page."""
    if not can_handle_type(db_user, type_):
        await message.answer(i18n.get("admin-only"))
        return
    rows, total = await SubmissionRepository(session).list_for_registry(
        type_=type_, status=None, order="desc", page=0, per_page=PER_PAGE,
    )
    total_pages = max((total + PER_PAGE - 1) // PER_PAGE, 1)
    text = (
        _render_list(i18n, type_, "all", "desc", rows)
        if rows else i18n.get("registry-empty")
    )
    await message.answer(
        text,
        reply_markup=registry_list_kb(
            i18n, type_=type_.value, status="all", order="desc", page=0,
            public_ids=[r.public_id for r in rows], total_pages=total_pages,
        ),
    )


async def refresh_detail(
    query: CallbackQuery, i18n: I18nContext, session, submission_id: int
) -> bool:
    """Redraw the detail screen after an action taken from the registry.

    No-op (returns False) when the click came from a push card, which
    responsible.py updates through its own update_all_cards path.
    """
    markup = query.message.reply_markup if query.message else None
    if markup is None:
        return False
    back = None
    for row in markup.inline_keyboard:
        for button in row:
            if button.callback_data and button.callback_data.startswith("reg:"):
                back = RegistryCb.unpack(button.callback_data)
                break
        if back is not None:
            break
    if back is None:
        return False

    sub = await session.get(Submission, submission_id)
    if sub is None:
        return False
    await session.refresh(sub)
    await _edit(
        query,
        split_text(_render_detail(i18n, sub))[0],
        registry_detail_kb(
            i18n, submission_id=sub.id, type_=back.type, status=back.status,
            order=back.order, page=back.page, status_value=sub.status.value,
        ),
    )
    return True


@router.callback_query(MenuCb.filter(F.action.in_({"reg_appeal", "reg_corruption"})))
async def on_menu_registry(
    query: CallbackQuery, callback_data: MenuCb, i18n: I18nContext,
    db_user: User, session,
) -> None:
    type_ = (
        SubmissionType.appeal
        if callback_data.action == "reg_appeal"
        else SubmissionType.corruption
    )
    await open_registry(query.message, i18n, db_user, session, type_)
    await query.answer()


@router.message(Command("appeals"))
async def cmd_appeals(message: Message, i18n: I18nContext, db_user: User, session) -> None:
    await open_registry(message, i18n, db_user, session, SubmissionType.appeal)


@router.message(Command("complaints"))
async def cmd_complaints(message: Message, i18n: I18nContext, db_user: User, session) -> None:
    await open_registry(message, i18n, db_user, session, SubmissionType.corruption)
