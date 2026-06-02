"""FSM state groups for the multi-step forms."""
from __future__ import annotations

from aiogram.fsm.state import State, StatesGroup


class SubmissionForm(StatesGroup):
    anonymous = State()  # corruption only: ask anonymity first
    name = State()
    phone = State()
    text = State()
    attachments = State()
    confirm = State()


class ResponseForm(StatesGroup):
    text = State()  # responsible writing a reply to the applicant
