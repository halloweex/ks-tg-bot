"""Common command handlers — /start and other global commands."""
from __future__ import annotations

from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from bot import texts
from bot.config import AppConfig
from bot.db import get_user_phone
from bot.states import OnboardingStates

router = Router()


@router.message(CommandStart())
async def cmd_start(
    message: Message, config: AppConfig, state: FSMContext
) -> None:
    """Handle /start command — greet new users or welcome back returning ones."""
    # Always clear any active FSM state (e.g. user sends /start mid-onboarding)
    await state.clear()

    # Returning user — already verified, skip phone entry
    phone = await get_user_phone(message.chat.id)
    if phone:
        await message.answer(texts.MSG_WELCOME_BACK)
        return

    # New user — send branded greeting (includes phone prompt) and enter FSM
    greeting = texts.GREETING.format(brand_name=config.brand_name)
    await message.answer(greeting)
    await state.set_state(OnboardingStates.waiting_phone)
