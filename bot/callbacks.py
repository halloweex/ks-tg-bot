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
    """Which order the digest draws in full, and what else it shows.

    `order_id` is the local cache row id, and 0 means "the newest on this
    page". The id only ever selects among the requester's own cached orders, so
    it cannot be used to reach someone else's (the list is always re-read for
    that chat).

    `state` carries what the screen is showing besides the card — "c" while the
    cancelled orders are unfolded. It rides along on every button so that
    pressing any of them keeps the rest of the screen as it was.
    """

    action: str
    order_id: int = 0
    page: int = 0
    state: str = ""


class StockAction(CallbackData, prefix="stk"):
    """Back-in-stock subscription: sub / unsub, for one sku."""

    action: str
    sku: str


class DiscountAction(CallbackData, prefix="disc"):
    """Customer asking for a discount on the products they buy most.

    `sku` names one product when the ask came from its card in the inline list,
    and is empty when it came from the favourites screen, where the button sits
    under the whole list and means all of it.
    """

    action: str
    sku: str = ""


class DeliveryAction(CallbackData, prefix="dlvr"):
    """Delivery tracking actions: view, refresh."""

    action: str


class BroadcastAction(CallbackData, prefix="bcast"):
    """Admin broadcast confirmation: send, cancel."""

    action: str
