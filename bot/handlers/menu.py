"""The main menu — both of them — and what their entries open.

The menu exists twice, in the two forms Telegram offers, because neither can do
the other's job. The keyboard under the input field is what draws the ☰ toggle
in the input row and never scrolls away; the same menu as buttons in a message
is the only one that can hand the input field to the inline list, which is what
«⭐ Улюблені» does there (bot/keyboards.py, bot/handlers/inline.py).

A reply keyboard sends the button's own label as an ordinary message, so those
entries are matched on text — in every language the label can be rendered in,
because the keyboard on someone's screen may predate their language change. The
inline menu arrives as callbacks instead, and the pair below each section does
the same thing from either.

Each entry opens a *section*: the bot sends the screen as a new message, and
from there the section's own inline buttons edit that message in place
(`bot/screen.py`). Sending rather than editing is what leaves the menu message
itself intact — tapping «📦 Замовлення» must not consume the menu the customer
will want again, and «⭐ Улюблені» lives on it.

No Back button anywhere: neither menu ever leaves the screen.
"""
from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from core.i18n import Texts, variants
from bot.callbacks import InfoAction, MenuAction
from bot.analytics import track
from core.config import AppConfig
from bot.handlers.delivery import delivery_screen
from bot.handlers.orders import favourites_screen, orders_screen
from bot.keyboards import info_menu_kb, settings_menu_kb, website_kb
from bot.screen import render, send_main_menu
from core.adapters.keycrm.client import KeyCRMClient
from core.adapters.novaposhta.client import NovaPoshtaClient
from bot.states import OnboardingStates, SettingsStates, SupportStates

router = Router()

# While someone is sharing a phone number, the keyboard on their screen is the
# share-phone one and these labels can only arrive as typed text. Letting them
# through would abandon the flow halfway — and clearing the state below would
# leave a shared contact with no handler waiting for it.
_NOT_SHARING_PHONE = ~StateFilter(
    OnboardingStates.waiting_phone, SettingsStates.waiting_new_phone
)


def _menu(key: str):
    """A handler filter for one key of the menu keyboard."""
    return router.message(_NOT_SHARING_PHONE, F.text.in_(variants(key)))


@_menu("BTN_ORDERS")
async def open_orders(
    message: Message,
    state: FSMContext,
    keycrm: KeyCRMClient,
    t: Texts,
) -> None:
    """📦 — the order history, newest first."""
    await state.clear()
    text, markup = await orders_screen(message.chat.id, t, keycrm, message)
    await message.answer(text, reply_markup=markup)


@_menu("BTN_DELIVERY_STATUS")
async def open_delivery(
    message: Message,
    state: FSMContext,
    novaposhta: NovaPoshtaClient | None,
    t: Texts,
) -> None:
    """🚚 — where every parcel with a tracking number is right now."""
    await state.clear()
    text, markup = await delivery_screen(message.chat.id, t, novaposhta, message)
    await message.answer(text, reply_markup=markup)


@_menu("BTN_FAVOURITES")
async def open_favourites(
    message: Message,
    state: FSMContext,
    keycrm: KeyCRMClient,
    config: AppConfig,
    t: Texts,
) -> None:
    """⭐ — what this customer buys most, and what of it is out of stock."""
    await state.clear()
    text, markup = await favourites_screen(
        message.chat.id, t, keycrm, message, config.website_url
    )
    await message.answer(text, reply_markup=markup)


@_menu("BTN_SUPPORT")
async def open_support(message: Message, state: FSMContext, t: Texts) -> None:
    """💬 — hand the conversation to a person."""
    track(message.chat.id, "support_opened")
    await state.set_state(SupportStates.waiting_message)
    await message.answer(t.MSG_SUPPORT_PROMPT)


@_menu("BTN_INFO")
async def open_info(message: Message, state: FSMContext, t: Texts) -> None:
    """ℹ️ — the four pages from config.yaml."""
    await state.clear()
    await message.answer(t.MSG_INFO_MENU, reply_markup=info_menu_kb(t))


@_menu("BTN_SETTINGS")
async def open_settings(message: Message, state: FSMContext, t: Texts) -> None:
    """⚙️ — phone number and language."""
    await state.clear()
    await message.answer(t.MSG_SETTINGS_MENU, reply_markup=settings_menu_kb(t))


# Same guard as every key below: mid-onboarding the menu would replace the
# share-phone keyboard, and the keys it puts there are filtered out until the
# number is shared — a menu that answers nothing.
@router.message(Command("menu"), _NOT_SHARING_PHONE)
@_menu("BTN_MENU")
async def restore_menu(message: Message, config: AppConfig, t: Texts) -> None:
    """/menu, and «📋 Меню» — the single button older versions put on the
    keyboard.

    The key is kept because a keyboard sent months ago is still on someone's
    screen, and tapping it should bring the current menu rather than nothing.
    Answering with the keyboard replaces the old one with it.

    The command is how the menu in the message is brought back: it is an
    ordinary message and scrolls away like any other, and «⭐ Улюблені» on it
    is the only way into the inline list that does not cost an extra tap.
    """
    await send_main_menu(message, t, config.website_url)


@_menu("BTN_WEBSITE")
async def open_website(message: Message, config: AppConfig, t: Texts) -> None:
    """🌐 — the shop link, as a message: a reply button cannot carry a URL."""
    await message.answer(
        t.MSG_WEBSITE_INTRO, reply_markup=website_kb(t, config.website_url)
    )


@router.callback_query(MenuAction.filter(F.action == "open_orders"))
async def orders_from_menu(
    callback: CallbackQuery,
    state: FSMContext,
    keycrm: KeyCRMClient,
    t: Texts,
) -> None:
    """📦 from the menu in the message. Same screen as the key below it."""
    await callback.answer()
    await state.clear()
    text, markup = await orders_screen(
        callback.from_user.id, t, keycrm, callback.message
    )
    await callback.message.answer(text, reply_markup=markup)


@router.callback_query(MenuAction.filter(F.action == "open_delivery"))
async def delivery_from_menu(
    callback: CallbackQuery,
    state: FSMContext,
    novaposhta: NovaPoshtaClient | None,
    t: Texts,
) -> None:
    """🚚 from the menu in the message."""
    await callback.answer()
    await state.clear()
    text, markup = await delivery_screen(
        callback.from_user.id, t, novaposhta, callback.message
    )
    await callback.message.answer(text, reply_markup=markup)


@router.callback_query(MenuAction.filter(F.action == "open_settings"))
async def settings_from_menu(
    callback: CallbackQuery, state: FSMContext, t: Texts
) -> None:
    """⚙️ from the menu in the message."""
    await callback.answer()
    await state.clear()
    await callback.message.answer(t.MSG_SETTINGS_MENU, reply_markup=settings_menu_kb(t))


@router.callback_query(MenuAction.filter(F.action == "open_info"))
async def info_from_menu(callback: CallbackQuery, state: FSMContext, t: Texts) -> None:
    """ℹ️ from the menu in the message."""
    await callback.answer()
    await state.clear()
    await callback.message.answer(t.MSG_INFO_MENU, reply_markup=info_menu_kb(t))


@router.callback_query(MenuAction.filter(F.action == "open_support"))
async def support_from_menu(
    callback: CallbackQuery, state: FSMContext, t: Texts
) -> None:
    """💬 from the menu in the message."""
    await callback.answer()
    track(callback.from_user.id, "support_opened")
    await state.set_state(SupportStates.waiting_message)
    await callback.message.answer(t.MSG_SUPPORT_PROMPT)


@router.callback_query(MenuAction.filter(F.action == "info"))
async def show_info_menu(callback: CallbackQuery, t: Texts) -> None:
    """Back from an info page to the list of pages — edited in place, since
    both belong to the same screen."""
    await callback.answer()
    await render(callback, t.MSG_INFO_MENU, info_menu_kb(t))


@router.callback_query(MenuAction.filter(F.action == "support"))
async def support_from_screen(
    callback: CallbackQuery, state: FSMContext, t: Texts
) -> None:
    """The support button offered on the "we found no orders" screen."""
    await callback.answer()
    track(callback.from_user.id, "support_opened")
    await state.set_state(SupportStates.waiting_message)
    await render(callback, t.MSG_SUPPORT_PROMPT)


@router.callback_query(InfoAction.filter(F.page == "back"))
async def info_back(callback: CallbackQuery, t: Texts) -> None:
    """Older messages may still carry this button; keep it working."""
    await callback.answer()
    await render(callback, t.MSG_INFO_MENU, info_menu_kb(t))
