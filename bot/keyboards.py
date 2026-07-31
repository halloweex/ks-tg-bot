"""Inline keyboard builders for menu navigation."""
from __future__ import annotations

from urllib.parse import urlencode, urlparse

from aiogram.types import InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.callbacks import BroadcastAction, DeliveryAction, InfoAction, MenuAction, SettingsAction
from bot.i18n import LANGUAGE_NAMES, SUPPORTED, Texts


def tagged_website_url(url: str) -> str:
    """Website URL with UTM tags.

    Telegram sends no event when a `url=` button is tapped — there is no callback
    to hook. Tagging the link is the only way these clicks can ever be counted,
    and it happens in the shop's analytics, not here.
    """
    if not url:
        return url
    tags = urlencode({
        "utm_source": "telegram",
        "utm_medium": "bot",
        "utm_campaign": "main_menu",
    })
    return f"{url}{'&' if urlparse(url).query else '?'}{tags}"


def share_phone_kb(t: Texts) -> ReplyKeyboardMarkup:
    """Reply keyboard with the single request_contact button.

    request_contact is the only way to prove phone ownership: Telegram fills in
    contact.user_id with the sender's own id, which handlers verify.
    """
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=t.BTN_SHARE_PHONE, request_contact=True)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def main_menu_kb(t: Texts, website_url: str) -> InlineKeyboardMarkup:
    """Build the main menu inline keyboard (7 buttons in a 2+2+3 grid).

    Seven buttons stacked one per row filled a phone screen and made every
    option look equally important. The grid puts the two questions people
    actually arrive with — where is my order, when does it come — side by side
    at the top, what they might do next below, and the rarely-tapped three on
    the last row.
    """
    builder = InlineKeyboardBuilder()
    builder.button(text=t.BTN_ORDERS, callback_data=MenuAction(action="orders"))
    builder.button(text=t.BTN_DELIVERY_STATUS, callback_data=DeliveryAction(action="view"))
    builder.button(text=t.BTN_FAVOURITES, callback_data=MenuAction(action="favourites"))
    builder.button(text=t.BTN_SUPPORT, callback_data=MenuAction(action="support"))
    builder.button(text=t.BTN_WEBSITE, url=tagged_website_url(website_url))
    builder.button(text=t.BTN_INFO, callback_data=MenuAction(action="info"))
    builder.button(text=t.BTN_SETTINGS, callback_data=MenuAction(action="settings"))
    builder.adjust(2, 2, 3)
    return builder.as_markup()


def menu_only_kb(t: Texts) -> InlineKeyboardMarkup:
    """A screen whose only way on is back to the menu.

    Replaces the reply keyboard the dead-end screens used to carry: with a menu
    button beside the input field, a second permanent «📋 Меню» underneath was
    two navigations for one bot, and the reply keyboard ate a third of a phone
    screen to say one word.
    """
    builder = InlineKeyboardBuilder()
    builder.button(text=t.BTN_MENU, callback_data=MenuAction(action="back"))
    return builder.as_markup()


def info_menu_kb(t: Texts) -> InlineKeyboardMarkup:
    """Build the info submenu inline keyboard (4 items + back, 2+2+1 layout)."""
    builder = InlineKeyboardBuilder()
    builder.button(text=t.BTN_ABOUT, callback_data=InfoAction(page="about"))
    builder.button(text=t.BTN_CONTACTS, callback_data=InfoAction(page="contacts"))
    builder.button(text=t.BTN_PAYMENT, callback_data=InfoAction(page="payment"))
    builder.button(text=t.BTN_DELIVERY, callback_data=InfoAction(page="delivery"))
    builder.button(text=t.BTN_BACK, callback_data=InfoAction(page="back"))
    builder.adjust(2, 2, 1)
    return builder.as_markup()


def broadcast_confirm_kb(t: Texts) -> InlineKeyboardMarkup:
    """Yes/No confirmation for the admin broadcast flow."""
    builder = InlineKeyboardBuilder()
    builder.button(text=t.BTN_BROADCAST_YES, callback_data=BroadcastAction(action="send"))
    builder.button(text=t.BTN_BROADCAST_NO, callback_data=BroadcastAction(action="cancel"))
    builder.adjust(2)
    return builder.as_markup()


def settings_menu_kb(t: Texts) -> InlineKeyboardMarkup:
    """Build the settings submenu inline keyboard (2 items + back, 2+1 layout)."""
    builder = InlineKeyboardBuilder()
    builder.button(text=t.BTN_CHANGE_PHONE, callback_data=SettingsAction(action="phone"))
    builder.button(text=t.BTN_LANGUAGE, callback_data=SettingsAction(action="language"))
    builder.button(text=t.BTN_BACK, callback_data=SettingsAction(action="back"))
    builder.adjust(2, 1)
    return builder.as_markup()


def language_kb(current: str) -> InlineKeyboardMarkup:
    """One button per supported language, ticking the active one."""
    builder = InlineKeyboardBuilder()
    for code, name in LANGUAGE_NAMES.items():
        mark = " ✅" if code == current else ""
        builder.button(text=f"{name}{mark}", callback_data=SettingsAction(action="lang", value=code))
    builder.adjust(len(SUPPORTED))
    return builder.as_markup()
