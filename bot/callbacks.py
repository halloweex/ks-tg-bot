"""CallbackData factory classes for inline menu actions."""
from __future__ import annotations

from aiogram.filters.callback_data import CallbackData


class MenuAction(CallbackData, prefix="menu"):
    """Main menu actions: orders, info, support, settings, back."""

    action: str


class InfoAction(CallbackData, prefix="info"):
    """Info submenu pages: about, contacts, payment, delivery, back."""

    page: str


class SettingsAction(CallbackData, prefix="sett"):
    """Settings submenu actions: phone, language, back."""

    action: str


class DeliveryAction(CallbackData, prefix="dlvr"):
    """Delivery tracking actions: view, refresh."""

    action: str


class BroadcastAction(CallbackData, prefix="bcast"):
    """Admin broadcast confirmation: send, cancel."""

    action: str
