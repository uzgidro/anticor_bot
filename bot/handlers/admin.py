"""Admin commands: assign / list / revoke responsible roles.

Every handler is gated by IsAdmin (re-checked per update). Role changes are
written to the audit log. Promotion of bootstrap admins is also audited here on
first /assign use is not needed — UserMiddleware handles is_admin; we audit the
role grants the admin performs.
"""
from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message
from aiogram_i18n import I18nContext
from sqlalchemy import or_, select

from bot.db.models import SubmissionType, User
from bot.db.repositories import AuditRepository, UserRepository
from bot.filters.roles import IsAdmin
from bot.keyboards.inline import AssignTypeCb, RevokeCb, assign_type_keyboard
from bot.utils.text import escape

router = Router(name="admin")
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())


def _target_tg_id(message: Message) -> int | None:
    """Extract the target user's tg_id from a forwarded message or an argument."""
    if message.forward_from is not None:
        return message.forward_from.id
    parts = (message.text or "").split()
    if len(parts) >= 2 and parts[1].lstrip("-").isdigit():
        return int(parts[1])
    return None


@router.message(Command("assign"))
async def cmd_assign(message: Message, i18n: I18nContext, session) -> None:
    tg_id = _target_tg_id(message)
    if tg_id is None:
        await message.answer(i18n.get("admin-assign-usage"))
        return
    user, _ = await UserRepository(session).get_or_create(tg_id=tg_id)
    await message.answer(
        i18n.get("admin-assign-choose-type"),
        reply_markup=assign_type_keyboard(i18n, user.id),
    )


@router.callback_query(AssignTypeCb.filter())
async def on_assign_type(
    query: CallbackQuery, callback_data: AssignTypeCb, db_user: User,
    i18n: I18nContext, session,
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
    await query.message.edit_text(i18n.get("admin-assigned", user=str(target.tg_id)))
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
        name = escape(u.full_name) if u.full_name else str(u.tg_id)
        lines.append(f"• {name} — {', '.join(roles)}")
        if u.resp_appeal:
            kb.button(
                text=f"🗑 {name}: {i18n.get('type-appeal')}",
                callback_data=RevokeCb(user_id=u.id, type=SubmissionType.appeal.value),
            )
        if u.resp_corruption:
            kb.button(
                text=f"🗑 {name}: {i18n.get('type-corruption')}",
                callback_data=RevokeCb(user_id=u.id, type=SubmissionType.corruption.value),
            )
    kb.adjust(1)
    await message.answer("\n".join(lines), reply_markup=kb.as_markup())


@router.callback_query(RevokeCb.filter())
async def on_revoke(
    query: CallbackQuery, callback_data: RevokeCb, db_user: User,
    i18n: I18nContext, session,
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
    await query.answer(i18n.get("admin-revoked"))
