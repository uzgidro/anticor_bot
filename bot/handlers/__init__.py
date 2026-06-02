"""Root router aggregating all handler routers.

Routers are included in priority order. The cancel handler (StateFilter('*'))
and the error router are added first so they take precedence over form states.
Concrete sub-routers are wired in Waves 3-4.
"""
from __future__ import annotations

from aiogram import Router

router = Router(name="root")

# Sub-routers are included here as they are implemented:
#   from bot.handlers import start, language, submission, my_submissions,
#                            responsible, admin, errors
#   router.include_routers(errors.router, start.router, ...)
