"""FSM state groups for bot conversation flows."""
from __future__ import annotations

from aiogram.fsm.state import State, StatesGroup


class OnboardingStates(StatesGroup):
    """States for the phone verification onboarding flow."""

    waiting_phone = State()
