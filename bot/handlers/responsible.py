"""Responsible-person reactions: take in progress, reply, close.

Authorization is enforced per click at the object level (the user must be
responsible for THIS submission's type, or an admin) — callback data is
client-supplied and never trusted alone. Status changes are atomic; on a
successful claim/close every delivered card is updated for all responsibles.
"""
from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from aiogram_i18n import I18nContext

from bot.config import Settings
from bot.db.models import User
from bot.db.repositories import AuditRepository, SubmissionRepository
from bot.filters.roles import can_handle_type
from bot.handlers.states import ResponseForm
from bot.keyboards.inline import ReactionCb
from bot.security.crypto import AnonCipher
from bot.services.submissions import SubmissionService, safe_send
from bot.utils.text import escape

router = Router(name="responsible")


def _service(session, settings: Settings) -> SubmissionService:
    return SubmissionService(session, AnonCipher(settings.anon_enc_key))


async def _authorize(
    query: CallbackQuery, submission_id: int, db_user: User, session, i18n: I18nContext,
    *, require_owner: bool = False,
):
    """Return the submission if the user may act on it, else answer and None.

    Authorization is derived server-side: the user must be responsible for THIS
    submission's type (or admin). When ``require_owner`` is set (reply/close),
    the user must also be the assignee (the one who took it) or an admin — the
    model is first-claim ownership, so colleagues can't act on each other's
    cases. Callback data is never trusted for authz.
    """
    sub = await SubmissionRepository(session).get(submission_id)
    if sub is None or not can_handle_type(db_user, sub.type):
        await query.answer(i18n.get("admin-only"), show_alert=True)
        return None
    if require_owner and not db_user.is_admin:
        if sub.assigned_to_user_id not in (None, db_user.id):
            await query.answer(i18n.get("admin-only"), show_alert=True)
            return None
    return sub


@router.callback_query(ReactionCb.filter(F.action == "take"))
async def on_take(
    query: CallbackQuery, callback_data: ReactionCb, db_user: User, session,
    i18n: I18nContext, settings: Settings,
) -> None:
    svc = _service(session, settings)
    sub = await _authorize(query, callback_data.submission_id, db_user, session, i18n)
    if sub is None:
        return
    won = await svc.repo.try_claim(sub.id, db_user.id)
    if not won:
        # Someone else already took it; tell this user who.
        fresh = await session.get(type(sub), sub.id, populate_existing=True)
        assignee = (
            await session.get(User, fresh.assigned_to_user_id)
            if fresh and fresh.assigned_to_user_id else None
        )
        name = escape(assignee.full_name) if assignee and assignee.full_name else "—"
        await query.answer(i18n.get("cb-already-taken", name=name), show_alert=True)
        return
    await svc.repo.record_status_event(sub.id, db_user.id, "new", "in_progress")
    await AuditRepository(session).log(
        action="status_change", actor_user_id=db_user.id, target=f"submission:{sub.id}",
        meta="new->in_progress",
    )
    await session.commit()
    # Update the card for everyone, showing who took it.
    taker = escape(db_user.full_name) if db_user.full_name else "—"
    await svc.update_all_cards(query.bot, i18n.core, sub, "card-assigned", name=taker)
    await session.commit()
    await query.answer(i18n.get("cb-taken"))


@router.callback_query(ReactionCb.filter(F.action == "close"))
async def on_close(
    query: CallbackQuery, callback_data: ReactionCb, db_user: User, session,
    i18n: I18nContext, settings: Settings,
) -> None:
    svc = _service(session, settings)
    sub = await _authorize(
        query, callback_data.submission_id, db_user, session, i18n, require_owner=True
    )
    if sub is None:
        return
    prev = await svc.repo.close(sub.id, db_user.id)
    if prev is None:
        await query.answer(i18n.get("cb-closed"))
        return
    await svc.repo.record_status_event(sub.id, db_user.id, prev, "closed")
    await AuditRepository(session).log(
        action="status_change", actor_user_id=db_user.id, target=f"submission:{sub.id}",
        meta=f"{prev}->closed",
    )
    await session.commit()
    await svc.update_all_cards(
        query.bot, i18n.core, sub, "status-closed"
    )
    # Notify the applicant their submission was closed.
    chat_id = await svc.resolve_author_chat_id(sub)
    if chat_id is not None:
        locale = await _author_locale(session, sub)
        text = i18n.core.get("submission-closed-notify", locale, public_id=sub.public_id)
        await safe_send(query.bot, chat_id, text)
    await session.commit()
    await query.answer(i18n.get("cb-closed"))


@router.callback_query(ReactionCb.filter(F.action == "reply"))
async def on_reply_start(
    query: CallbackQuery, callback_data: ReactionCb, db_user: User, session,
    i18n: I18nContext, state: FSMContext, settings: Settings,
) -> None:
    sub = await _authorize(
        query, callback_data.submission_id, db_user, session, i18n, require_owner=True
    )
    if sub is None:
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
    state: FSMContext, settings: Settings,
) -> None:
    data = await state.get_data()
    submission_id = data["submission_id"]
    await state.clear()
    svc = _service(session, settings)
    sub = await svc.repo.get(submission_id)
    if sub is None or not can_handle_type(db_user, sub.type):
        await message.answer(i18n.get("admin-only"))
        return

    from bot.db.models import SubmissionResponse

    session.add(
        SubmissionResponse(
            submission_id=sub.id, responder_user_id=db_user.id, text=message.text
        )
    )
    await AuditRepository(session).log(
        action="reply", actor_user_id=db_user.id, target=f"submission:{sub.id}"
    )
    await session.commit()

    chat_id = await svc.resolve_author_chat_id(sub)
    if chat_id is not None:
        locale = await _author_locale(session, sub)
        header = i18n.core.get("reply-to-author", locale, public_id=sub.public_id)
        body = i18n.core.get("reply-to-author-body", locale, text=escape(message.text))
        await safe_send(message.bot, chat_id, f"{header}\n{body}")
    await message.answer(i18n.get("reply-sent"))


async def _author_locale(session, sub) -> str:
    """Resolve the applicant's locale (for non-anonymous); default ru otherwise."""
    if sub.author_user_id is not None:
        author = await session.get(User, sub.author_user_id)
        if author and author.language:
            return author.language
    return "ru"
