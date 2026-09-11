"""Settings handlers — phone change FSM flow and language display."""
from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from core.i18n import Texts, normalize, variants
from bot.callbacks import SettingsAction
from bot.analytics import track
from core.config import AppConfig
from core.repos.users import save_user, set_user_language
from bot.handlers.onboarding import own_contact_phone
from bot.keyboards import language_kb, menu_kb, share_phone_kb
from bot.screen import render, send_main_menu
from bot.states import SettingsStates, SupportStates

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
        await message.answer(
            t.ERR_INVALID_PHONE, reply_markup=share_phone_kb(t, with_manager=True)
        )
        return

    await save_user(message.chat.id, phone.e164)
    await state.clear()
    # Sending the menu keyboard replaces the share-phone one it is answering.
    await send_main_menu(message, t, config, t.MSG_PHONE_CHANGED)


@router.message(StateFilter(None), F.contact)
async def contact_with_nobody_waiting_for_it(
    message: Message,
    config: AppConfig,
    t: Texts,
) -> None:
    """A number shared when no flow asked for one.

    **How she gets here.** «⚙️ Налаштування» → «📱 Змінити номер» sends a NEW
    message carrying the share-phone reply keyboard, and the settings screen
    stays live above it. She taps «📋 Меню» there — which clears the state —
    and the reply keyboard is still under her input field, because a keyboard
    under the input field cannot be taken away by editing a message. Then she
    taps the button the bot drew for her, and until now nothing happened at
    all: both `F.contact` handlers are behind a state filter, and no route
    existed for a contact outside one. She proved her number and got silence.

    **What it does instead.** The same thing the flow would have: saves it and
    puts the menu back. `send_main_menu` carries a ReplyKeyboardRemove when
    `bottom_menu` is off, which is what finally clears the stray keyboard —
    the one thing an edit could never do.

    **Ownership is not re-checked here, and that is deliberate.**
    `own_contact_phone` returns None unless the contact's `user_id` is the
    sender's own (core.domain.phone), so a forwarded card cannot become
    somebody's verified number by arriving on this route. A second check
    written out here would be a second place to get it wrong. Silence is the
    right answer for that case: a contact card sent for any other reason is
    not addressed to us, and answering it would be the bot butting in.
    """
    phone = own_contact_phone(message)
    if not phone:
        return

    await save_user(message.chat.id, phone.e164)
    track(message.chat.id, "phone_shared", source="stray_keyboard")
    await send_main_menu(message, t, config, t.MSG_PHONE_CHANGED)


@router.message(SettingsStates.waiting_new_phone, F.text.in_(variants("BTN_SUPPORT")))
async def escape_to_support(message: Message, state: FSMContext, t: Texts) -> None:
    """The same exit as in onboarding, for the same unreadable number.

    Changing a phone hits the identical wall: the contact parses or it does
    not, and the customer cannot type their way around it.
    """
    track(message.chat.id, "support_opened", source="share_phone")
    await state.set_state(SupportStates.waiting_message)
    await message.answer(t.MSG_SUPPORT_PROMPT, reply_markup=menu_kb(t))


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
    await render(callback, t.MSG_LANGUAGE_CHOOSE, language_kb(lang, t))


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

    # Both menus carry the button labels, so a language change has to send them
    # again — an edit cannot touch the keyboard under the input field, and the
    # menu in the message is the one this callback just overwrote.
    t = Texts(chosen)
    await render(callback, t.MSG_LANGUAGE_SET)
    await send_main_menu(callback.message, t, config)
