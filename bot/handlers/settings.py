"""Settings handlers — phone change FSM flow and language display."""
from __future__ import annotations

import re

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot import texts
from bot.callbacks import SettingsAction
from bot.config import AppConfig
from bot.db import save_user
from bot.handlers.onboarding import PHONE_PATTERN
from bot.keyboards import main_menu_kb
from bot.states import SettingsStates

router = Router()


@router.callback_query(SettingsAction.filter(F.action == "phone"))
async def start_phone_change(
    callback: CallbackQuery,
    callback_data: SettingsAction,
    state: FSMContext,
) -> None:
    """Prompt user to enter a new phone number."""
    await callback.answer()
    await state.set_state(SettingsStates.waiting_new_phone)
    await callback.message.edit_text(texts.MSG_NEW_PHONE_PROMPT)


@router.message(SettingsStates.waiting_new_phone)
async def process_new_phone(
    message: Message,
    state: FSMContext,
    config: AppConfig,
) -> None:
    """Validate new phone and save it, or show error for retry."""
    raw = message.text or ""
    phone = re.sub(r"[\s\-\(\)]", "", raw.strip())

    if not PHONE_PATTERN.match(phone):
        await message.answer(texts.ERR_INVALID_PHONE)
        return

    await save_user(message.chat.id, phone)
    await state.clear()
    await message.answer(
        texts.MSG_PHONE_CHANGED,
        reply_markup=main_menu_kb(config.website_url),
    )


@router.callback_query(SettingsAction.filter(F.action == "language"))
async def show_language(
    callback: CallbackQuery,
    callback_data: SettingsAction,
) -> None:
    """Show current language as a popup alert (stays on settings menu)."""
    await callback.answer(texts.MSG_LANGUAGE_CURRENT, show_alert=True)
