"""Root router aggregating all handler routers.

Routers are included in priority order. The cancel handler (StateFilter('*'))
and the error router are added first so they take precedence over form states.
Concrete sub-routers are wired in Waves 3-4.
"""
from __future__ import annotations

from aiogram import Router

from bot.handlers import my_submissions, start, submission

router = Router(name="root")

# Order matters: form/submission routers with explicit StateFilters take
# precedence; start/menu handle the rest. (errors, responsible, admin join in
# Wave 4.)
router.include_router(submission.router)
router.include_router(my_submissions.router)
router.include_router(start.router)
