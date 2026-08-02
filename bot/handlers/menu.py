"""The main menu: the keyboard under the input field, and what its keys open.

A reply keyboard sends the button's own label as an ordinary message, so every
entry here is matched on text — in every language the label can be rendered in,
because the keyboard on someone's screen may predate their language change.

Each key opens a *section*: the bot sends the screen as a new message, and from
there the section's own inline buttons edit that message in place
(`bot/screen.py`). There is no inline main menu any more and no Back button
anywhere: the menu never leaves the screen, so there is nothing to return to.
"""
from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from core.i18n import Texts, variants
from bot.callbacks import InfoAction, MenuAction
from bot.analytics import track
from core.config import AppConfig
from bot.handlers.delivery import delivery_screen
from bot.handlers.orders import favourites_screen, orders_screen
from bot.keyboards import (info_menu_kb, main_menu_kb, settings_menu_kb,
                           website_kb)
from bot.screen import render
from core.adapters.keycrm.client import KeyCRMClient
from bot.services.novaposhta import NovaPoshtaClient
from bot.services.shopify import ShopifyClient
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
    shopify: ShopifyClient | None,
    t: Texts,
) -> None:
    """📦 — the order history, newest first."""
    await state.clear()
    text, markup = await orders_screen(message.chat.id, t, keycrm, shopify, message)
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
    shopify: ShopifyClient | None,
    t: Texts,
) -> None:
    """⭐ — what this customer buys most, and what of it is out of stock."""
    await state.clear()
    text, markup = await favourites_screen(message.chat.id, t, keycrm, shopify, message)
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


@_menu("BTN_MENU")
async def restore_menu(message: Message, config: AppConfig, t: Texts) -> None:
    """«📋 Меню» — the single button older versions put on the keyboard.

    Kept because a keyboard sent months ago is still on someone's screen, and
    tapping it should bring the current menu rather than nothing. Answering
    with the keyboard replaces the old one with it.
    """
    await message.answer(t.MSG_MAIN_MENU, reply_markup=main_menu_kb(t))


@_menu("BTN_WEBSITE")
async def open_website(message: Message, config: AppConfig, t: Texts) -> None:
    """🌐 — the shop link, as a message: a reply button cannot carry a URL."""
    await message.answer(
        t.MSG_WEBSITE_INTRO, reply_markup=website_kb(t, config.website_url)
    )


@router.callback_query(MenuAction.filter(F.action == "info"))
async def show_info_menu(callback: CallbackQuery, t: Texts) -> None:
    """Back from an info page to the list of pages."""
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
