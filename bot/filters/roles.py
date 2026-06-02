"""Role-based filters.

These gate sensitive handlers. They read the ORM ``db_user`` placed in data by
UserMiddleware. Object-level authorization (does this responsible person have
rights to THIS submission) is enforced in the handlers/service, not here —
callback data is client-supplied and must never be trusted for authz alone.
"""
from __future__ import annotations

from aiogram.filters import BaseFilter
from aiogram.types import TelegramObject

from bot.db.models import SubmissionType, User


class IsAdmin(BaseFilter):
    async def __call__(self, event: TelegramObject, db_user: User | None = None) -> bool:
        return db_user is not None and db_user.is_admin


class IsResponsible(BaseFilter):
    """True if the user is responsible for the given submission type (or any)."""

    def __init__(self, type_: SubmissionType | None = None) -> None:
        self.type_ = type_

    async def __call__(self, event: TelegramObject, db_user: User | None = None) -> bool:
        if db_user is None:
            return False
        if self.type_ == SubmissionType.appeal:
            return bool(db_user.resp_appeal)
        if self.type_ == SubmissionType.corruption:
            return bool(db_user.resp_corruption)
        return bool(db_user.resp_appeal or db_user.resp_corruption)


def can_handle_type(user: User, type_: SubmissionType) -> bool:
    """Object-level check: may this user act on a submission of this type?"""
    if type_ == SubmissionType.appeal:
        return bool(user.resp_appeal or user.is_admin)
    return bool(user.resp_corruption or user.is_admin)
