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
    """Settings submenu actions: phone, language, lang (with a code), back.

    `value` carries the payload for actions that need one — the language code
    for action="lang". It cannot be packed into `action` itself: aiogram uses
    ':' as the field separator and rejects it inside a value.
    """

    action: str
    value: str = ""


class OrderAction(CallbackData, prefix="ord"):
    """Expand/collapse the item list of one order in the order list message.

    `order_id` is the local cache row id, and 0 means "nothing expanded". The id
    only ever selects among the requester's own cached orders, so it cannot be
    used to reach someone else's (the list is always re-read for that chat).
    """

    action: str
    order_id: int = 0


class DeliveryAction(CallbackData, prefix="dlvr"):
    """Delivery tracking actions: view, refresh."""

    action: str


class BroadcastAction(CallbackData, prefix="bcast"):
    """Admin broadcast confirmation: send, cancel."""

    action: str
