"""Citizen submission FSM: appeal / corruption form with navigation & validation."""
from __future__ import annotations

import re

from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from aiogram_i18n import I18nContext

from bot.config import Settings
from bot.db.models import AttachmentType, SubmissionType, User
from bot.handlers.states import SubmissionForm
from bot.keyboards.inline import (
    AnonCb,
    FormCb,
    MenuCb,
    anon_keyboard,
    attachments_keyboard,
    confirm_keyboard,
    contact_keyboard,
    nav_keyboard,
    remove_reply_keyboard,
)
from bot.services.submissions import (
    AttachmentInput,
    SubmissionInput,
    SubmissionService,
)
from bot.utils.text import MAX_INPUT_LEN, escape, split_text

router = Router(name="submission")

_PHONE_RE = re.compile(r"^\+?\d[\d\-\s()]{5,}\d$")


# ---------- entry ----------

@router.callback_query(MenuCb.filter(F.action.in_({"appeal", "corruption"})))
async def start_form(
    query: CallbackQuery, callback_data: MenuCb, state: FSMContext, i18n: I18nContext
) -> None:
    type_ = SubmissionType(callback_data.action)
    await state.set_data({"type": type_.value, "attachments": []})
    if type_ == SubmissionType.corruption:
        await state.set_state(SubmissionForm.anonymous)
        await query.message.answer(
            f"{i18n.get('form-anonymous-ask')}\n{i18n.get('form-anonymous-warning')}",
            reply_markup=anon_keyboard(i18n),
        )
    else:
        await state.update_data(is_anonymous=False)
        await _ask_name(query.message, state, i18n)
    await query.answer()


@router.callback_query(SubmissionForm.anonymous, AnonCb.filter())
async def on_anonymous(
    query: CallbackQuery, callback_data: AnonCb, state: FSMContext, i18n: I18nContext
) -> None:
    await state.update_data(is_anonymous=callback_data.value)
    if callback_data.value:
        await _ask_text(query.message, state, i18n)  # skip name/phone
    else:
        await _ask_name(query.message, state, i18n)
    await query.answer()


# ---------- steps ----------

async def _ask_name(message: Message, state: FSMContext, i18n: I18nContext) -> None:
    await state.set_state(SubmissionForm.name)
    await message.answer(i18n.get("form-ask-name"), reply_markup=nav_keyboard(i18n, back=False))


async def _ask_phone(message: Message, state: FSMContext, i18n: I18nContext) -> None:
    await state.set_state(SubmissionForm.phone)
    # The "share contact" button must live on a ReplyKeyboard (Telegram only
    # allows request_contact there), so the phone step uses that; back/cancel
    # remain available via the inline nav shown on the preceding step and the
    # /cancel command.
    await message.answer(i18n.get("form-ask-phone"), reply_markup=contact_keyboard(i18n))


async def _ask_text(message: Message, state: FSMContext, i18n: I18nContext) -> None:
    await state.set_state(SubmissionForm.text)
    data = await state.get_data()
    prompt = i18n.get("form-ask-text")
    if data.get("is_anonymous"):
        prompt = f"{prompt}\n{i18n.get('form-text-anon-hint')}"
    await message.answer(prompt, reply_markup=nav_keyboard(i18n))


async def _ask_attachments(message: Message, state: FSMContext, i18n: I18nContext) -> None:
    await state.set_state(SubmissionForm.attachments)
    await message.answer(i18n.get("form-ask-attachments"), reply_markup=attachments_keyboard(i18n))


@router.message(SubmissionForm.name, F.text)
async def on_name(message: Message, state: FSMContext, i18n: I18nContext) -> None:
    if len(message.text) > MAX_INPUT_LEN:
        await message.answer(i18n.get("form-too-long", max=MAX_INPUT_LEN))
        return
    await state.update_data(full_name=message.text.strip())
    await _ask_phone(message, state, i18n)


@router.message(SubmissionForm.name)
async def on_name_invalid(message: Message, i18n: I18nContext) -> None:
    await message.answer(i18n.get("form-invalid-text"))


@router.message(SubmissionForm.phone, F.contact)
async def on_phone_contact(message: Message, state: FSMContext, i18n: I18nContext) -> None:
    await state.update_data(phone=message.contact.phone_number)
    # Drop the one-time contact reply keyboard before the next inline step.
    await message.answer("✅", reply_markup=remove_reply_keyboard())
    await _ask_text(message, state, i18n)


@router.message(SubmissionForm.phone, F.text)
async def on_phone_text(message: Message, state: FSMContext, i18n: I18nContext) -> None:
    if not _PHONE_RE.match(message.text.strip()):
        await message.answer(i18n.get("form-invalid-phone"))
        return
    await state.update_data(phone=message.text.strip())
    await message.answer("✅", reply_markup=remove_reply_keyboard())
    await _ask_text(message, state, i18n)


@router.message(SubmissionForm.text, F.text)
async def on_text(message: Message, state: FSMContext, i18n: I18nContext) -> None:
    if len(message.text) > MAX_INPUT_LEN:
        await message.answer(i18n.get("form-too-long", max=MAX_INPUT_LEN))
        return
    await state.update_data(text=message.text.strip())
    await _ask_attachments(message, state, i18n)


@router.message(SubmissionForm.text)
async def on_text_invalid(message: Message, i18n: I18nContext) -> None:
    await message.answer(i18n.get("form-invalid-text"))


@router.message(SubmissionForm.attachments, F.photo | F.document)
async def on_attachment(
    message: Message, state: FSMContext, i18n: I18nContext, album: list[Message] | None = None
) -> None:
    data = await state.get_data()
    atts: list[dict] = data.get("attachments", [])
    # An album arrives as several messages; the MediaGroupMiddleware aggregates
    # them and injects ``album``. A single photo/doc has album=None.
    messages = album if album else [message]
    for msg in messages:
        if msg.photo:
            atts.append({"file_id": msg.photo[-1].file_id, "type": AttachmentType.photo.value})
        elif msg.document:
            atts.append({"file_id": msg.document.file_id, "type": AttachmentType.document.value})
    await state.update_data(attachments=atts)
    await message.answer(
        i18n.get("form-attachment-added", count=len(atts)),
        reply_markup=attachments_keyboard(i18n),
    )


@router.message(SubmissionForm.attachments)
async def on_attachment_invalid(message: Message, i18n: I18nContext) -> None:
    # Non-photo/document input at the attachments step: guide, don't get stuck.
    await message.answer(i18n.get("form-ask-attachments"))


# ---------- navigation ----------

@router.callback_query(StateFilter(SubmissionForm), FormCb.filter(F.action == "cancel"))
async def on_cancel(query: CallbackQuery, state: FSMContext, i18n: I18nContext) -> None:
    await state.clear()
    await query.message.answer(i18n.get("cancelled"))
    await query.answer()


@router.message(Command("cancel"), StateFilter(SubmissionForm))
async def cmd_cancel(message: Message, state: FSMContext, i18n: I18nContext) -> None:
    await state.clear()
    await message.answer(i18n.get("cancelled"))


@router.callback_query(SubmissionForm.attachments, FormCb.filter(F.action.in_({"done", "skip"})))
async def on_attachments_done(query: CallbackQuery, state: FSMContext, i18n: I18nContext) -> None:
    await _show_confirm(query.message, state, i18n)
    await query.answer()


@router.callback_query(StateFilter(SubmissionForm), FormCb.filter(F.action == "back"))
async def on_back(query: CallbackQuery, state: FSMContext, i18n: I18nContext) -> None:
    """Go to the previous step, honoring anonymous mode (name/phone skipped)."""
    current = await state.get_state()
    data = await state.get_data()
    is_anon = bool(data.get("is_anonymous"))
    msg = query.message
    if current == SubmissionForm.phone.state:
        await _ask_name(msg, state, i18n)
    elif current == SubmissionForm.text.state:
        # Anonymous skipped name/phone -> back goes to the anonymity question.
        if is_anon:
            await state.set_state(SubmissionForm.anonymous)
            await msg.answer(
                f"{i18n.get('form-anonymous-ask')}\n{i18n.get('form-anonymous-warning')}",
                reply_markup=anon_keyboard(i18n),
            )
        else:
            await _ask_phone(msg, state, i18n)
    elif current == SubmissionForm.attachments.state:
        await _ask_text(msg, state, i18n)
    elif current == SubmissionForm.confirm.state:
        await _ask_attachments(msg, state, i18n)
    await query.answer()


async def _show_confirm(message: Message, state: FSMContext, i18n: I18nContext) -> None:
    data = await state.get_data()
    type_ = SubmissionType(data["type"])
    type_label = i18n.get(f"type-{type_.value}")
    lines = [i18n.get("form-summary"), i18n.get("form-summary-type", type=type_label)]
    if data.get("is_anonymous"):
        lines.append(i18n.get("form-summary-anonymous"))
    else:
        if data.get("full_name"):
            lines.append(i18n.get("form-summary-name", name=escape(data["full_name"])))
        if data.get("phone"):
            lines.append(i18n.get("form-summary-phone", phone=escape(data["phone"])))
    lines.append(i18n.get("form-summary-text", text=escape(data.get("text", ""))))
    lines.append(i18n.get("form-summary-attachments", count=len(data.get("attachments", []))))
    await state.set_state(SubmissionForm.confirm)
    summary = "\n".join(lines)
    parts = split_text(summary)
    for part in parts[:-1]:
        await message.answer(part)
    await message.answer(parts[-1], reply_markup=confirm_keyboard(i18n))


@router.callback_query(SubmissionForm.confirm, FormCb.filter(F.action == "submit"))
async def on_submit(
    query: CallbackQuery,
    state: FSMContext,
    i18n: I18nContext,
    db_user: User,
    session,
    settings: Settings,
) -> None:
    from bot.security.crypto import AnonCipher

    data = await state.get_data()
    if "type" not in data:
        # State already consumed (e.g. a rapid double-tap on Submit). Ignore the
        # duplicate so we never create two submissions / two fan-outs.
        await query.answer()
        return
    type_ = SubmissionType(data["type"])
    # Clear state up front so a concurrent second tap hits the guard above.
    await state.clear()
    cipher = AnonCipher(settings.anon_enc_key)
    svc = SubmissionService(session, cipher)
    sub = await svc.create(
        SubmissionInput(
            type=type_,
            text=data.get("text", ""),
            is_anonymous=bool(data.get("is_anonymous")),
            author_tg_id=db_user.tg_id,
            author_user_id=db_user.id,
            full_name=data.get("full_name"),
            phone=data.get("phone"),
            attachments=[
                AttachmentInput(a["file_id"], AttachmentType(a["type"]))
                for a in data.get("attachments", [])
            ],
        )
    )

    # Commit BEFORE any network fan-out: responsibles must never receive a card
    # for a submission that a later rollback would erase. Capture ids first
    # (the instance may expire after commit).
    public_id = sub.public_id
    await session.commit()

    delivered = await svc.dispatch_to_responsibles(
        query.bot, i18n.core, sub, default_locale=settings.default_locale
    )
    await session.commit()  # persist SubmissionDelivery rows

    if delivered:
        await query.message.answer(
            f"{i18n.get('submission-accepted')}\n"
            f"{i18n.get('submission-accepted-ticket', public_id=public_id)}\n"
            f"{i18n.get('submission-accepted-note')}"
        )
    else:
        await query.message.answer(
            i18n.get("submission-accepted-no-responsible", public_id=public_id)
        )
        await _alert_admins_no_responsible(query.bot, svc, i18n, sub, settings)
    await query.answer()


async def _alert_admins_no_responsible(bot, svc, i18n, sub, settings) -> None:
    from sqlalchemy import select

    from bot.db.models import User as U

    type_label = i18n.get(f"type-{sub.type.value}")
    admins = list(await svc.session.scalars(select(U).where(U.is_admin.is_(True))))
    for admin in admins:
        locale = admin.language or settings.default_locale
        text = i18n.core.get(
            "new-submission-admin-alert", locale, public_id=sub.public_id, type=type_label
        )
        try:
            await bot.send_message(admin.tg_id, text)
        except Exception:
            continue
