"""Root router aggregating all handler routers.

Routers are included in priority order. The cancel handler (StateFilter('*'))
and the error router are added first so they take precedence over form states.
Concrete sub-routers are wired in Waves 3-4.
"""
from __future__ import annotations

from aiogram import Router

from bot.handlers import admin, my_submissions, responsible, start, submission

router = Router(name="root")

# Order matters: admin (role-gated) and the FSM form/response routers with
# explicit StateFilters take precedence; start/menu handle the rest.
router.include_router(admin.router)
router.include_router(responsible.router)
router.include_router(submission.router)
router.include_router(my_submissions.router)
router.include_router(start.router)

# The error handler is registered on this root router by the factory (which has
# the i18n core for localized notices) via errors.register_errors(router, core).
# It must live on the root — a leaf sub-router's @errors() never catches siblings.
