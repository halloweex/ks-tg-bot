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

A key below the input field opens a *section* as a new message: the tap already
put the customer's own message in the chat, and there is nothing on screen to
edit. A tap in the menu message edits that message instead — menu → довідка →
оплата is one bubble changing three times, not three bubbles.

Which means the menu message is consumed by the section it opens, so every
screen reachable from it carries «📋 Меню» to bring it back in place. The
keyboard below the input field is the other way back, and the one that always
works; it just costs a new message.
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
from bot.handlers.invite import invite_screen
from bot.handlers.orders import (favourites_screen, follow_up_parcel,
                                 orders_screen)
from bot.keyboards import (info_menu_kb, main_menu_inline_kb, menu_kb,
                           settings_menu_kb, website_kb)
from bot import rich
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
    novaposhta: NovaPoshtaClient | None,
    config: AppConfig,
    t: Texts,
) -> None:
    """📦 — the order history, newest first."""
    await state.clear()
    screen = await orders_screen(message.chat.id, t, keycrm, message, config)
    sent = await rich.send(message.bot, message.chat.id, screen.blocks or [],
                           plain=screen.text, reply_markup=screen.markup)
    # And a moment later, where the parcel is — from the carrier, in the
    # background, so this screen still opens from the cache instantly.
    follow_up_parcel(sent, message.chat.id, t, novaposhta)


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
    screen = await favourites_screen(
        message.chat.id, t, keycrm, message, config.website_url, config
    )
    await rich.send(message.bot, message.chat.id, screen.blocks or [],
                    plain=screen.text, reply_markup=screen.markup)


@_menu("BTN_SUPPORT")
async def open_support(message: Message, state: FSMContext, t: Texts) -> None:
    """💬 — hand the conversation to a person."""
    track(message.chat.id, "support_opened")
    await state.set_state(SupportStates.waiting_message)
    await message.answer(t.MSG_SUPPORT_PROMPT, reply_markup=menu_kb(t))


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
    await send_main_menu(message, t, config)


@_menu("BTN_INVITE")
async def open_invite(message: Message, state: FSMContext, config: AppConfig,
                      t: Texts) -> None:
    """🎁 — the referral screen: the link, and how it is going."""
    await state.clear()
    text, markup = await invite_screen(message.chat.id, t, config)
    await message.answer(text, reply_markup=markup)


@router.callback_query(MenuAction.filter(F.action == "open_invite"))
async def invite_from_menu(callback: CallbackQuery, state: FSMContext,
                           config: AppConfig, t: Texts) -> None:
    """🎁 from the menu in the message."""
    await callback.answer()
    await state.clear()
    text, markup = await invite_screen(callback.from_user.id, t, config)
    await render(callback, text, markup)


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
    novaposhta: NovaPoshtaClient | None,
    config: AppConfig,
    t: Texts,
) -> None:
    """📦 from the menu in the message. Same screen as the key below it."""
    await callback.answer()
    await state.clear()
    screen = await orders_screen(
        callback.from_user.id, t, keycrm, callback.message, config
    )
    follow_up_parcel(
        await render(callback, screen.text, screen.markup, blocks=screen.blocks),
        callback.from_user.id, t, novaposhta)


@router.callback_query(MenuAction.filter(F.action == "open_favourites"))
async def favourites_from_menu(
    callback: CallbackQuery,
    state: FSMContext,
    keycrm: KeyCRMClient,
    config: AppConfig,
    t: Texts,
) -> None:
    """⭐ from the menu in the message. Same screen as the key below it.

    It used to open the inline list straight from here, which made the two
    halves of one menu behave differently. The list is still one tap away, on
    the screen this opens, where it is next to what it searches.
    """
    await callback.answer()
    await state.clear()
    screen = await favourites_screen(
        callback.from_user.id, t, keycrm, callback.message, config.website_url,
        config
    )
    await render(callback, screen.text, screen.markup, blocks=screen.blocks)


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
    await render(callback, text, markup)


@router.callback_query(MenuAction.filter(F.action == "open_settings"))
async def settings_from_menu(
    callback: CallbackQuery, state: FSMContext, t: Texts
) -> None:
    """⚙️ from the menu in the message."""
    await callback.answer()
    await state.clear()
    await render(callback, t.MSG_SETTINGS_MENU, settings_menu_kb(t))


@router.callback_query(MenuAction.filter(F.action == "open_info"))
async def info_from_menu(callback: CallbackQuery, state: FSMContext, t: Texts) -> None:
    """ℹ️ from the menu in the message."""
    await callback.answer()
    await state.clear()
    await render(callback, t.MSG_INFO_MENU, info_menu_kb(t))


@router.callback_query(MenuAction.filter(F.action == "open_support"))
async def support_from_menu(
    callback: CallbackQuery, state: FSMContext, t: Texts
) -> None:
    """💬 from the menu in the message."""
    await callback.answer()
    track(callback.from_user.id, "support_opened")
    await state.set_state(SupportStates.waiting_message)
    await render(callback, t.MSG_SUPPORT_PROMPT, menu_kb(t))


@router.callback_query(MenuAction.filter(F.action == "menu"))
async def back_to_menu(
    callback: CallbackQuery, state: FSMContext, config: AppConfig, t: Texts
) -> None:
    """«📋 Меню» — the menu again, in the message the section is occupying.

    The section replaced the menu when it opened; this puts it back. Sending a
    new one instead would leave the section on screen above it, which is the
    trail all of this is here to avoid.
    """
    await callback.answer()
    await state.clear()
    await render(callback, t.MSG_MENU_PICK,
                 main_menu_inline_kb(t, config.website_url))


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
    await render(callback, t.MSG_SUPPORT_PROMPT, menu_kb(t))


@router.callback_query(InfoAction.filter(F.page == "back"))
async def info_back(callback: CallbackQuery, t: Texts) -> None:
    """Older messages may still carry this button; keep it working."""
    await callback.answer()
    await render(callback, t.MSG_INFO_MENU, info_menu_kb(t))
