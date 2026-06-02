"""'My submissions' — list the user's own submissions and statuses.

Anonymous corruption complaints are intentionally NOT listed: showing them
against the account would defeat the anonymity guarantee.
"""
from __future__ import annotations

from aiogram import F, Router
from aiogram.types import CallbackQuery
from aiogram_i18n import I18nContext
from sqlalchemy import select

from bot.db.models import Submission, User
from bot.keyboards.inline import MenuCb

router = Router(name="my_submissions")


@router.callback_query(MenuCb.filter(F.action == "my"))
async def on_my_submissions(
    query: CallbackQuery, i18n: I18nContext, db_user: User, session
) -> None:
    rows = list(
        await session.scalars(
            select(Submission)
            .where(
                Submission.author_user_id == db_user.id,
                Submission.is_anonymous.is_(False),
            )
            .order_by(Submission.created_at.desc())
            .limit(20)
        )
    )
    if not rows:
        await query.message.answer(i18n.get("my-submissions-empty"))
        await query.answer()
        return

    lines = [i18n.get("my-submissions-title")]
    for sub in rows:
        type_label = i18n.get(f"type-{sub.type.value}")
        status_label = i18n.get(f"status-{sub.status.value}")
        lines.append(
            i18n.get(
                "my-submission-item",
                public_id=sub.public_id,
                type=type_label,
                status=status_label,
            )
        )
    await query.message.answer("\n".join(lines))
    await query.answer()
