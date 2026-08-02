"""Settings handlers — phone change FSM flow and language display."""
from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from core.i18n import Texts, normalize
from bot.callbacks import SettingsAction
from bot.analytics import track
from core.config import AppConfig
from core.repos.users import save_user, set_user_language
from bot.handlers.onboarding import own_contact_phone
from bot.keyboards import language_kb, main_menu_kb, share_phone_kb
from bot.screen import render
from bot.states import SettingsStates

router = Router()


@router.callback_query(SettingsAction.filter(F.action == "phone"))
async def start_phone_change(
    callback: CallbackQuery,
    callback_data: SettingsAction,
    state: FSMContext,
    t: Texts,
) -> None:
    """Ask the user to re-share their number via the request_contact button."""
    await callback.answer()
    await state.set_state(SettingsStates.waiting_new_phone)
    await callback.message.answer(
        t.MSG_NEW_PHONE_PROMPT, reply_markup=share_phone_kb(t)
    )


@router.message(SettingsStates.waiting_new_phone, F.contact)
async def process_new_contact(
    message: Message,
    state: FSMContext,
    config: AppConfig,
    t: Texts,
) -> None:
    """Update the phone only from the user's OWN verified contact."""
    if message.contact and message.contact.user_id != (message.from_user.id if message.from_user else None):
        await message.answer(t.ERR_CONTACT_NOT_OWN, reply_markup=share_phone_kb(t))
        return

    phone = own_contact_phone(message)
    if not phone:
        await message.answer(t.ERR_INVALID_PHONE, reply_markup=share_phone_kb(t))
        return

    await save_user(message.chat.id, phone)
    await state.clear()
    # Sending the menu keyboard replaces the share-phone one it is answering.
    await message.answer(
        f"{t.MSG_PHONE_CHANGED}\n\n{t.MSG_MAIN_MENU}", reply_markup=main_menu_kb(t)
    )


@router.message(SettingsStates.waiting_new_phone)
async def reject_typed_new_phone(message: Message, t: Texts) -> None:
    """Refuse manually typed numbers when changing the phone."""
    await message.answer(t.MSG_USE_SHARE_BUTTON, reply_markup=share_phone_kb(t))


@router.callback_query(SettingsAction.filter(F.action == "language"))
async def show_language(
    callback: CallbackQuery,
    callback_data: SettingsAction,
    t: Texts,
    lang: str,
) -> None:
    """Offer the supported languages, ticking the active one."""
    await callback.answer()
    await render(callback, t.MSG_LANGUAGE_CHOOSE, language_kb(lang))


@router.callback_query(SettingsAction.filter(F.action == "lang"))
async def set_language(
    callback: CallbackQuery,
    callback_data: SettingsAction,
    config: AppConfig,
) -> None:
    """Persist the chosen language and redraw the menu in it.

    `t` from the middleware still holds the OLD language — this request was
    resolved before the choice was stored — so build a fresh one.
    """
    chosen = normalize(callback_data.value)
    await set_user_language(callback.from_user.id, chosen)
    track(callback.from_user.id, "language_changed", to=chosen)
    await callback.answer()

    # The keyboard carries the button labels, so a language change has to send
    # a new one — an edit cannot touch the keyboard under the input field.
    t = Texts(chosen)
    await render(callback, t.MSG_LANGUAGE_SET)
    await callback.message.answer(t.MSG_MAIN_MENU, reply_markup=main_menu_kb(t))
