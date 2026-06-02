"""Root router aggregating all handler routers.

Routers are included in priority order. The cancel handler (StateFilter('*'))
and the error router are added first so they take precedence over form states.
Concrete sub-routers are wired in Waves 3-4.
"""
from __future__ import annotations

from aiogram import Router

from bot.handlers import admin, errors, my_submissions, responsible, start, submission

router = Router(name="root")

# Order matters: admin (role-gated) and the FSM form/response routers with
# explicit StateFilters take precedence; start/menu handle the rest. The errors
# router registers the @errors() handler (order-independent for error events).
router.include_router(errors.router)
router.include_router(admin.router)
router.include_router(responsible.router)
router.include_router(submission.router)
router.include_router(my_submissions.router)
router.include_router(start.router)
