"""FSM state groups for bot conversation flows."""
from __future__ import annotations

from aiogram.fsm.state import State, StatesGroup


class OnboardingStates(StatesGroup):
    """States for the phone verification onboarding flow."""

    waiting_phone = State()


class SupportStates(StatesGroup):
    """States for the support message flow."""

    waiting_message = State()


class SettingsStates(StatesGroup):
    """States for the settings change flows."""

    waiting_new_phone = State()


class BroadcastStates(StatesGroup):
    """States for the admin broadcast flow."""

    waiting_message = State()
    waiting_confirm = State()
