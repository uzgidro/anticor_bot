"""Admin commands: assign / list / revoke responsible roles.

Every handler is gated by IsAdmin (re-checked per update). Role changes are
written to the audit log. Promotion of bootstrap admins is also audited here on
first /assign use is not needed — UserMiddleware handles is_admin; we audit the
role grants the admin performs.
"""
from __future__ import annotations

import re

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message
from aiogram_i18n import I18nContext
from sqlalchemy import or_, select

from bot.config import Settings
from bot.db.models import SubmissionType, User
from bot.db.repositories import AuditRepository, UserRepository
from bot.filters.roles import IsAdmin
from bot.keyboards.inline import AssignTypeCb, RevokeCb, assign_type_keyboard
from bot.runners.commands import set_personal_commands
from bot.utils.text import escape

router = Router(name="admin")
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())


_MATRIX_ID_RE = re.compile(r"^@[^:\s]+:[^\s]+$")


def _target(message: Message) -> tuple[str, int | str] | None:
    """Who the admin is assigning: ('tg', id) from a forwarded message or a
    numeric argument, ('matrix', '@user:server') from a Matrix id argument."""
    if message.forward_from is not None:
        return "tg", message.forward_from.id
    parts = (message.text or "").split()
    if len(parts) < 2:
        return None
    arg = parts[1]
    if arg.lstrip("-").isdigit():
        return "tg", int(arg)
    if _MATRIX_ID_RE.match(arg):
        return "matrix", arg
    return None


@router.message(Command("assign"))
async def cmd_assign(message: Message, i18n: I18nContext, session) -> None:
    target = _target(message)
    if target is None:
        await message.answer(i18n.get("admin-assign-usage"))
        return
    kind, ident = target
    repo = UserRepository(session)
    if kind == "tg":
        user, _ = await repo.get_or_create(tg_id=ident)
    else:
        user, _ = await repo.get_or_create_by_matrix_id(ident)
    await message.answer(
        i18n.get("admin-assign-choose-type"),
        reply_markup=assign_type_keyboard(i18n, user.id),
    )


@router.callback_query(AssignTypeCb.filter())
async def on_assign_type(
    query: CallbackQuery, callback_data: AssignTypeCb, db_user: User,
    i18n: I18nContext, session, settings: Settings,
) -> None:
    target = await session.get(User, callback_data.user_id)
    if target is None:
        await query.answer(i18n.get("error-generic"), show_alert=True)
        return
    type_ = SubmissionType(callback_data.type)
    if type_ == SubmissionType.appeal:
        target.resp_appeal = True
    else:
        target.resp_corruption = True
    await AuditRepository(session).log(
        action="assign_role", actor_user_id=db_user.id,
        target=f"user:{target.id}", meta=f"+{type_.value}",
    )
    await session.commit()
    await _refresh_commands(query, target, i18n.core, settings.default_locale)
    label = str(target.tg_id or target.matrix_id)
    await query.message.edit_text(i18n.get("admin-assigned", user=label))
    await query.answer()


@router.message(Command("responsibles"))
async def cmd_responsibles(message: Message, i18n: I18nContext, session) -> None:
    rows = list(
        await session.scalars(
            select(User).where(or_(User.resp_appeal.is_(True), User.resp_corruption.is_(True)))
        )
    )
    if not rows:
        await message.answer(i18n.get("admin-responsibles-empty"))
        return
    from aiogram.utils.keyboard import InlineKeyboardBuilder

    lines = [i18n.get("admin-responsibles-title")]
    kb = InlineKeyboardBuilder()
    for u in rows:
        roles = []
        if u.resp_appeal:
            roles.append(i18n.get("type-appeal"))
        if u.resp_corruption:
            roles.append(i18n.get("type-corruption"))
        name = escape(u.full_name) if u.full_name else str(u.tg_id or u.matrix_id)
        lines.append(f"• {name} — {', '.join(roles)}")
        revoke = i18n.get("btn-revoke")
        if u.resp_appeal:
            kb.button(
                text=f"{revoke}: {name} / {i18n.get('type-appeal')}",
                callback_data=RevokeCb(user_id=u.id, type=SubmissionType.appeal.value),
            )
        if u.resp_corruption:
            kb.button(
                text=f"{revoke}: {name} / {i18n.get('type-corruption')}",
                callback_data=RevokeCb(user_id=u.id, type=SubmissionType.corruption.value),
            )
    kb.adjust(1)
    await message.answer("\n".join(lines), reply_markup=kb.as_markup())


@router.callback_query(RevokeCb.filter())
async def on_revoke(
    query: CallbackQuery, callback_data: RevokeCb, db_user: User,
    i18n: I18nContext, session, settings: Settings,
) -> None:
    target = await session.get(User, callback_data.user_id)
    if target is None:
        await query.answer(i18n.get("error-generic"), show_alert=True)
        return
    type_ = SubmissionType(callback_data.type)
    if type_ == SubmissionType.appeal:
        target.resp_appeal = False
    else:
        target.resp_corruption = False
    await AuditRepository(session).log(
        action="revoke_role", actor_user_id=db_user.id,
        target=f"user:{target.id}", meta=f"-{type_.value}",
    )
    await session.commit()
    await _refresh_commands(query, target, i18n.core, settings.default_locale)
    await query.answer(i18n.get("admin-revoked"))


async def _refresh_commands(
    query: CallbackQuery, target: User, core, default_locale: str
) -> None:
    """Update the target user's personal '/' menu after a role change.

    Best-effort: a user who has never started the bot has no reachable chat
    scope, and that must never fail the role change itself.
    """
    from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

    if target.tg_id is None:
        return  # Matrix-only user: no Telegram chat scope to update
    try:
        await set_personal_commands(query.bot, core, target, default_locale)
    except (TelegramBadRequest, TelegramForbiddenError):
        pass
