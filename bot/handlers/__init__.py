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
# explicit StateFilters take precedence; start/menu handle the rest.
router.include_router(admin.router)
router.include_router(responsible.router)
router.include_router(submission.router)
router.include_router(my_submissions.router)
router.include_router(start.router)

# The error handler must live on the root router (attached to the dispatcher) to
# fire globally — a leaf sub-router's @errors() would never catch siblings.
errors.register_errors(router)
