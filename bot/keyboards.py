"""Inline keyboard builders for menu navigation."""
from __future__ import annotations

from urllib.parse import urlencode, urlparse

from aiogram.types import InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder

from bot.callbacks import BroadcastAction, InfoAction, SettingsAction
from core.i18n import LANGUAGE_NAMES, SUPPORTED, Texts


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


def main_menu_kb(t: Texts) -> ReplyKeyboardMarkup:
    """The main menu, as the keyboard under the input field.

    It is a reply keyboard and not an inline one for a reason that has nothing
    to do with taste: the square toggle in the input row — the thing people
    reach for to get the menu back — is drawn by the client only while a reply
    keyboard exists. No API creates it. An inline menu, however tidy, leaves
    that corner of the screen empty.

    `is_persistent` is deliberately **not** set. It sounds like what we want —
    "always show the keyboard" — but it is what takes the toggle icon away:
    per the API, with it off "the custom keyboard can be hidden and opened with
    a keyboard icon". The icon is the point. A menu that cannot be put away is
    also a menu that cannot be brought back.

    The placeholder replaces "Write a message" in a field we would rather
    nobody typed into.

    Three to a row is safe here, unlike inline: a reply keyboard spans the
    screen instead of the message bubble.

    «🌐 Сайт» is a key like the others because a reply button cannot carry a
    URL — pressing it makes the bot answer with the link.
    """
    builder = ReplyKeyboardBuilder()
    for label in (t.BTN_ORDERS, t.BTN_DELIVERY_STATUS,
                  t.BTN_FAVOURITES, t.BTN_SUPPORT,
                  t.BTN_WEBSITE, t.BTN_INFO, t.BTN_SETTINGS):
        builder.button(text=label)
    builder.adjust(2, 2, 3)
    return builder.as_markup(
        resize_keyboard=True,
        one_time_keyboard=False,
        input_field_placeholder=t.MSG_MENU_PLACEHOLDER,
    )


def website_kb(t: Texts, website_url: str) -> InlineKeyboardMarkup:
    """The shop link, which only an inline button can carry."""
    builder = InlineKeyboardBuilder()
    builder.button(text=t.BTN_WEBSITE, url=tagged_website_url(website_url))
    return builder.as_markup()


def info_menu_kb(t: Texts) -> InlineKeyboardMarkup:
    """Build the info submenu inline keyboard (4 pages, 2+2).

    No Back button: the main menu is on screen at all times now, under the
    input field, so there is nothing to go back *to*.
    """
    builder = InlineKeyboardBuilder()
    builder.button(text=t.BTN_ABOUT, callback_data=InfoAction(page="about"))
    builder.button(text=t.BTN_CONTACTS, callback_data=InfoAction(page="contacts"))
    builder.button(text=t.BTN_PAYMENT, callback_data=InfoAction(page="payment"))
    builder.button(text=t.BTN_DELIVERY, callback_data=InfoAction(page="delivery"))
    builder.adjust(2, 2)
    return builder.as_markup()


def broadcast_confirm_kb(t: Texts) -> InlineKeyboardMarkup:
    """Yes/No confirmation for the admin broadcast flow."""
    builder = InlineKeyboardBuilder()
    builder.button(text=t.BTN_BROADCAST_YES, callback_data=BroadcastAction(action="send"))
    builder.button(text=t.BTN_BROADCAST_NO, callback_data=BroadcastAction(action="cancel"))
    builder.adjust(2)
    return builder.as_markup()


def settings_menu_kb(t: Texts) -> InlineKeyboardMarkup:
    """Build the settings submenu inline keyboard (2 items, 1 per row).

    One per row because "Налаштування:" is a short message and an inline
    keyboard is only as wide as the bubble it hangs under — «📱 Змінити номер»
    does not survive being squeezed into half of it.
    """
    builder = InlineKeyboardBuilder()
    builder.button(text=t.BTN_CHANGE_PHONE, callback_data=SettingsAction(action="phone"))
    builder.button(text=t.BTN_LANGUAGE, callback_data=SettingsAction(action="language"))
    builder.adjust(1)
    return builder.as_markup()


def language_kb(current: str) -> InlineKeyboardMarkup:
    """One button per supported language, ticking the active one."""
    builder = InlineKeyboardBuilder()
    for code, name in LANGUAGE_NAMES.items():
        mark = " ✅" if code == current else ""
        builder.button(text=f"{name}{mark}", callback_data=SettingsAction(action="lang", value=code))
    builder.adjust(len(SUPPORTED))
    return builder.as_markup()
