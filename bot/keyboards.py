"""Inline keyboard builders for menu navigation."""
from __future__ import annotations

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot import texts
from bot.callbacks import DeliveryAction, InfoAction, MenuAction, SettingsAction


def main_menu_kb(website_url: str) -> InlineKeyboardMarkup:
    """Build the main menu inline keyboard (5 buttons, 1 per row).

    Buttons: Orders, Find more, Contact support, Website (URL), Settings.
    """
    builder = InlineKeyboardBuilder()
    builder.button(text=texts.BTN_ORDERS, callback_data=MenuAction(action="orders"))
    builder.button(text=texts.BTN_DELIVERY_STATUS, callback_data=DeliveryAction(action="view"))
    builder.button(text=texts.BTN_INFO, callback_data=MenuAction(action="info"))
    builder.button(text=texts.BTN_SUPPORT, callback_data=MenuAction(action="support"))
    builder.button(text=texts.BTN_WEBSITE, url=website_url)
    builder.button(text=texts.BTN_SETTINGS, callback_data=MenuAction(action="settings"))
    builder.adjust(1)
    return builder.as_markup()


def info_menu_kb() -> InlineKeyboardMarkup:
    """Build the info submenu inline keyboard (4 items + back, 2+2+1 layout)."""
    builder = InlineKeyboardBuilder()
    builder.button(text=texts.BTN_ABOUT, callback_data=InfoAction(page="about"))
    builder.button(text=texts.BTN_CONTACTS, callback_data=InfoAction(page="contacts"))
    builder.button(text=texts.BTN_PAYMENT, callback_data=InfoAction(page="payment"))
    builder.button(text=texts.BTN_DELIVERY, callback_data=InfoAction(page="delivery"))
    builder.button(text=texts.BTN_BACK, callback_data=InfoAction(page="back"))
    builder.adjust(2, 2, 1)
    return builder.as_markup()


def settings_menu_kb() -> InlineKeyboardMarkup:
    """Build the settings submenu inline keyboard (2 items + back, 1 per row)."""
    builder = InlineKeyboardBuilder()
    builder.button(text=texts.BTN_CHANGE_PHONE, callback_data=SettingsAction(action="phone"))
    builder.button(text=texts.BTN_LANGUAGE, callback_data=SettingsAction(action="language"))
    builder.button(text=texts.BTN_BACK, callback_data=SettingsAction(action="back"))
    builder.adjust(1)
    return builder.as_markup()
