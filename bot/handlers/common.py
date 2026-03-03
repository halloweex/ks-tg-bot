"""Common command handlers — /start and other global commands."""
from __future__ import annotations

from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import KeyboardButton, Message, ReplyKeyboardMarkup, ReplyKeyboardRemove

from bot import texts
from bot.config import AppConfig
from bot.db import get_user_phone
from bot.keyboards import main_menu_kb
from bot.states import OnboardingStates

router = Router()


@router.message(CommandStart())
async def cmd_start(
    message: Message, config: AppConfig, state: FSMContext
) -> None:
    """Handle /start command — greet new users or welcome back returning ones."""
    # Always clear any active FSM state (e.g. user sends /start mid-onboarding)
    await state.clear()

    # Returning user — already verified, show main menu
    phone = await get_user_phone(message.chat.id)
    if phone:
        await message.answer(texts.MSG_WELCOME_BACK, reply_markup=ReplyKeyboardRemove())
        await message.answer(texts.MSG_MAIN_MENU, reply_markup=main_menu_kb(config.website_url))
        return

    # New user — send greeting with share-phone keyboard
    greeting = texts.GREETING.format(brand_name=config.brand_name)
    keyboard = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=texts.BTN_SHARE_PHONE, request_contact=True)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )
    await message.answer(greeting, reply_markup=keyboard)
    await state.set_state(OnboardingStates.waiting_phone)
