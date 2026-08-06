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
    action: str  # appeal | corruption | my | language | reg_appeal | reg_corruption


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


def main_menu_keyboard(i18n, db_user=None) -> InlineKeyboardMarkup:
    """Main menu; registry entries appear only for the roles that may use them.

    ``db_user`` is optional so the citizen menu stays the default. Hiding a
    button is UX only — the handlers re-check the role on every click.
    """
    kb = InlineKeyboardBuilder()
    kb.button(text=i18n.get("btn-appeal"), callback_data=MenuCb(action="appeal"))
    kb.button(text=i18n.get("btn-corruption"), callback_data=MenuCb(action="corruption"))
    kb.button(text=i18n.get("btn-my-submissions"), callback_data=MenuCb(action="my"))
    if db_user is not None:
        if db_user.is_admin or db_user.resp_appeal:
            kb.button(
                text=i18n.get("btn-registry-appeals"),
                callback_data=MenuCb(action="reg_appeal"),
            )
        if db_user.is_admin or db_user.resp_corruption:
            kb.button(
                text=i18n.get("btn-registry-complaints"),
                callback_data=MenuCb(action="reg_corruption"),
            )
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


class RegistryCb(CallbackData, prefix="reg"):
    """Registry navigation state.

    The whole filter/sort/page state rides in the payload (48 bytes worst case
    against Telegram's 64-byte cap), so the registry needs no FSM and an old
    keyboard keeps working after a bot restart.

    ``open`` empty means the list view; otherwise it is the public_id to show.
    """

    type: str  # appeal | corruption — fixed on entry, never changes
    status: str  # all | new | in_progress | closed
    order: str  # desc | asc
    page: int
    open: str


# Status filter buttons: (i18n key, status value carried in callback data).
_STATUS_FILTERS = [
    ("btn-filter-all", "all"),
    ("btn-filter-new", "new"),
    ("btn-filter-in-progress", "in_progress"),
    ("btn-filter-closed", "closed"),
]


def registry_list_kb(
    i18n,
    *,
    type_: str,
    status: str,
    order: str,
    page: int,
    public_ids: list[str],
    total_pages: int,
) -> InlineKeyboardMarkup:
    """List view: open-buttons per row, status filters, sort toggle, paging."""
    kb = InlineKeyboardBuilder()

    def cb(**over) -> RegistryCb:
        base = {
            "type": type_, "status": status, "order": order, "page": page, "open": "",
        }
        return RegistryCb(**{**base, **over})

    for n, public_id in enumerate(public_ids, start=1):
        kb.button(text=str(n), callback_data=cb(open=public_id))
    kb.adjust(len(public_ids) or 1)

    filters = InlineKeyboardBuilder()
    for key, value in _STATUS_FILTERS:
        # Switching a filter resets to the first page: the old offset may not
        # exist in the new result set.
        filters.button(text=i18n.get(key), callback_data=cb(status=value, page=0))
    filters.adjust(4)
    kb.attach(filters)

    sort = InlineKeyboardBuilder()
    sort.button(text=i18n.get("btn-sort-newest"), callback_data=cb(order="desc", page=0))
    sort.button(text=i18n.get("btn-sort-oldest"), callback_data=cb(order="asc", page=0))
    sort.adjust(2)
    kb.attach(sort)

    if total_pages > 1:
        nav = InlineKeyboardBuilder()
        if page > 0:
            nav.button(text="◀️", callback_data=cb(page=page - 1))
        nav.button(
            text=i18n.get("registry-page", page=page + 1, pages=total_pages),
            callback_data=cb(),  # no-op label; tapping re-renders the same page
        )
        if page < total_pages - 1:
            nav.button(text="▶️", callback_data=cb(page=page + 1))
        nav.adjust(3)
        kb.attach(nav)

    return kb.as_markup()


def registry_detail_kb(
    i18n,
    *,
    submission_id: int,
    type_: str,
    status: str,
    order: str,
    page: int,
    status_value: str,
) -> InlineKeyboardMarkup:
    """Detail view: the SAME ReactionCb actions as the push card, plus Back.

    Actions are deliberately not new callbacks — responsible.py already handles
    ReactionCb with the authorization checks, and duplicating them would split
    authz across two code paths.
    """
    kb = InlineKeyboardBuilder()
    if status_value != "closed":
        if status_value == "new":
            kb.button(
                text=i18n.get("btn-take"),
                callback_data=ReactionCb(action="take", submission_id=submission_id),
            )
        kb.button(
            text=i18n.get("btn-reply"),
            callback_data=ReactionCb(action="reply", submission_id=submission_id),
        )
        kb.button(
            text=i18n.get("btn-close"),
            callback_data=ReactionCb(action="close", submission_id=submission_id),
        )
    kb.button(
        text=i18n.get("btn-back-to-list"),
        callback_data=RegistryCb(
            type=type_, status=status, order=order, page=page, open=""
        ),
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
