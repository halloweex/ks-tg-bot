"""Inline keyboard builders for menu navigation."""
from __future__ import annotations

from aiogram.types import InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.callbacks import BroadcastAction, DeliveryAction, InfoAction, MenuAction, SettingsAction
from bot.i18n import LANGUAGE_NAMES, SUPPORTED, Texts


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
    """Build the main menu inline keyboard (5 buttons, 1 per row).

    Buttons: Orders, Find more, Contact support, Website (URL), Settings.
    """
    builder = InlineKeyboardBuilder()
    builder.button(text=t.BTN_ORDERS, callback_data=MenuAction(action="orders"))
    builder.button(text=t.BTN_DELIVERY_STATUS, callback_data=DeliveryAction(action="view"))
    builder.button(text=t.BTN_INFO, callback_data=MenuAction(action="info"))
    builder.button(text=t.BTN_SUPPORT, callback_data=MenuAction(action="support"))
    builder.button(text=t.BTN_WEBSITE, url=website_url)
    builder.button(text=t.BTN_SETTINGS, callback_data=MenuAction(action="settings"))
    builder.adjust(1)
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
    """Build the settings submenu inline keyboard (2 items + back, 1 per row)."""
    builder = InlineKeyboardBuilder()
    builder.button(text=t.BTN_CHANGE_PHONE, callback_data=SettingsAction(action="phone"))
    builder.button(text=t.BTN_LANGUAGE, callback_data=SettingsAction(action="language"))
    builder.button(text=t.BTN_BACK, callback_data=SettingsAction(action="back"))
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
