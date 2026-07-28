"""Settings handlers — phone change FSM flow and language display."""
from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, ReplyKeyboardRemove

from bot import texts
from bot.callbacks import SettingsAction
from bot.config import AppConfig
from bot.db import save_user
from bot.handlers.onboarding import own_contact_phone
from bot.keyboards import main_menu_kb, share_phone_kb
from bot.states import SettingsStates

router = Router()


@router.callback_query(SettingsAction.filter(F.action == "phone"))
async def start_phone_change(
    callback: CallbackQuery,
    callback_data: SettingsAction,
    state: FSMContext,
) -> None:
    """Ask the user to re-share their number via the request_contact button."""
    await callback.answer()
    await state.set_state(SettingsStates.waiting_new_phone)
    await callback.message.answer(
        texts.MSG_NEW_PHONE_PROMPT, reply_markup=share_phone_kb()
    )


@router.message(SettingsStates.waiting_new_phone, F.contact)
async def process_new_contact(
    message: Message,
    state: FSMContext,
    config: AppConfig,
) -> None:
    """Update the phone only from the user's OWN verified contact."""
    if message.contact and message.contact.user_id != (message.from_user.id if message.from_user else None):
        await message.answer(texts.ERR_CONTACT_NOT_OWN, reply_markup=share_phone_kb())
        return

    phone = own_contact_phone(message)
    if not phone:
        await message.answer(texts.ERR_INVALID_PHONE, reply_markup=share_phone_kb())
        return

    await save_user(message.chat.id, phone)
    await state.clear()
    await message.answer(texts.MSG_PHONE_CHANGED, reply_markup=ReplyKeyboardRemove())
    await message.answer(texts.MSG_MAIN_MENU, reply_markup=main_menu_kb(config.website_url))


@router.message(SettingsStates.waiting_new_phone)
async def reject_typed_new_phone(message: Message) -> None:
    """Refuse manually typed numbers when changing the phone."""
    await message.answer(texts.MSG_USE_SHARE_BUTTON, reply_markup=share_phone_kb())


@router.callback_query(SettingsAction.filter(F.action == "language"))
async def show_language(
    callback: CallbackQuery,
    callback_data: SettingsAction,
) -> None:
    """Show current language as a popup alert (stays on settings menu)."""
    await callback.answer(texts.MSG_LANGUAGE_CURRENT, show_alert=True)
