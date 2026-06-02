"""Inline keyboards and CallbackData factories.

Button labels are localized via I18nContext at build time (the calling handler
passes the recipient's locale-bound i18n). CallbackData payloads carry only
non-sensitive ids/actions; authorization is re-checked server-side on click.
"""
from __future__ import annotations

from aiogram.filters.callback_data import CallbackData
from aiogram.types import (
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.db.models import SubmissionType

# Language codes and their human labels (shown in the language picker).
LANGUAGES: list[tuple[str, str]] = [
    ("ru", "🇷🇺 Русский"),
    ("uz_latn", "🇺🇿 Oʻzbekcha"),
    ("uz_cyrl", "🇺🇿 Ўзбекча"),
    ("kaa", "🇺🇿 Qaraqalpaqsha"),
    ("en", "🇬🇧 English"),
]


class LangCb(CallbackData, prefix="lang"):
    code: str


class MenuCb(CallbackData, prefix="menu"):
    action: str  # appeal | corruption | my | language


class AnonCb(CallbackData, prefix="anon"):
    value: bool


class FormCb(CallbackData, prefix="form"):
    action: str  # back | cancel | skip | done | submit | confirm_cancel | resume


class ReactionCb(CallbackData, prefix="react"):
    action: str  # take | reply | close
    submission_id: int


class AssignTypeCb(CallbackData, prefix="assign"):
    user_id: int
    type: str  # appeal | corruption


class RevokeCb(CallbackData, prefix="revoke"):
    user_id: int
    type: str


def language_keyboard() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for code, label in LANGUAGES:
        kb.button(text=label, callback_data=LangCb(code=code))
    kb.adjust(1)
    return kb.as_markup()


def main_menu_keyboard(i18n) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text=i18n.get("btn-appeal"), callback_data=MenuCb(action="appeal"))
    kb.button(text=i18n.get("btn-corruption"), callback_data=MenuCb(action="corruption"))
    kb.button(text=i18n.get("btn-my-submissions"), callback_data=MenuCb(action="my"))
    kb.button(text=i18n.get("btn-change-language"), callback_data=MenuCb(action="language"))
    kb.adjust(1)
    return kb.as_markup()


def anon_keyboard(i18n) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text=i18n.get("btn-yes"), callback_data=AnonCb(value=True))
    kb.button(text=i18n.get("btn-no"), callback_data=AnonCb(value=False))
    kb.button(text=i18n.get("btn-cancel"), callback_data=FormCb(action="cancel"))
    kb.adjust(2, 1)
    return kb.as_markup()


def nav_keyboard(i18n, *, skip: bool = False, back: bool = True) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    if skip:
        kb.button(text=i18n.get("btn-skip"), callback_data=FormCb(action="skip"))
    if back:
        kb.button(text=i18n.get("btn-back"), callback_data=FormCb(action="back"))
    kb.button(text=i18n.get("btn-cancel"), callback_data=FormCb(action="cancel"))
    kb.adjust(1)
    return kb.as_markup()


def attachments_keyboard(i18n) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text=i18n.get("btn-done"), callback_data=FormCb(action="done"))
    kb.button(text=i18n.get("btn-skip"), callback_data=FormCb(action="skip"))
    kb.button(text=i18n.get("btn-back"), callback_data=FormCb(action="back"))
    kb.button(text=i18n.get("btn-cancel"), callback_data=FormCb(action="cancel"))
    kb.adjust(2)
    return kb.as_markup()


def confirm_keyboard(i18n) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text=i18n.get("btn-submit"), callback_data=FormCb(action="submit"))
    kb.button(text=i18n.get("btn-back"), callback_data=FormCb(action="back"))
    kb.button(text=i18n.get("btn-cancel"), callback_data=FormCb(action="cancel"))
    kb.adjust(1)
    return kb.as_markup()


def reaction_keyboard(i18n, submission_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()

    def cb(action: str) -> ReactionCb:
        return ReactionCb(action=action, submission_id=submission_id)

    kb.button(text=i18n.get("btn-take"), callback_data=cb("take"))
    kb.button(text=i18n.get("btn-reply"), callback_data=cb("reply"))
    kb.button(text=i18n.get("btn-close"), callback_data=cb("close"))
    kb.adjust(1)
    return kb.as_markup()


def assign_type_keyboard(i18n, user_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(
        text=i18n.get("btn-resp-appeal"),
        callback_data=AssignTypeCb(user_id=user_id, type=SubmissionType.appeal.value),
    )
    kb.button(
        text=i18n.get("btn-resp-corruption"),
        callback_data=AssignTypeCb(user_id=user_id, type=SubmissionType.corruption.value),
    )
    kb.adjust(1)
    return kb.as_markup()


def contact_keyboard(i18n) -> ReplyKeyboardMarkup:
    """A one-shot reply keyboard offering to share the phone contact."""
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=i18n.get("btn-share-contact"), request_contact=True)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def remove_reply_keyboard() -> ReplyKeyboardRemove:
    return ReplyKeyboardRemove()
