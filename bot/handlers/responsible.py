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
        await query.answer(
            i18n.get("cb-already-taken", name=result.assignee_name), show_alert=True
        )
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
