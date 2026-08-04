"""What a scenario needs from a shop system, expressed without naming one.

Structural, not inherited: these are Protocols, so KeyCRMClient and ShopifyClient
satisfy them by having the right methods and never import this module. That is
deliberate — an adapter that has to inherit from a port ends up knowing about
the layer above it, and the direction of the dependency is the only thing this
package exists to control.

They are separate because the sources are not equal. All three answer with
orders; only KeyCRM knows who the buyer is and only KeyCRM can be asked what
changed since a moment in time, and a port that Shopify could not implement
would be a lie in the type.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from core.domain.order import Order


@runtime_checkable
class OrderSource(Protocol):
    """Somewhere a customer's orders can be read from by phone number."""

    async def get_orders_by_phone(self, phone: str) -> list[Order]:
        """Every order for this number. Empty list on failure, never raises."""
        ...


@runtime_checkable
class ChangedOrderFeed(Protocol):
    """Somewhere orders can be read by when they last changed, not by whose.

    Separate from OrderSource because the two are asked different questions and
    must fail differently. A customer's list is better half-filled than empty,
    so OrderSource swallows its errors; a sweep of a time window either covered
    the window or did not, and one that quietly returns half of it would have
    the cursor moved past the other half.
    """

    async def get_orders_changed_between(self, start: str, end: str) -> list[Order]:
        """Orders whose last change falls in [start, end], both bounds inclusive.

        Timestamps are UTC 'YYYY-MM-DD HH:MM:SS'. Raises on anything that means
        the window was not fully read.
        """
        ...


@runtime_checkable
class BuyerLookup(Protocol):
    """Somewhere a customer's profile can be read from by phone number."""

    async def get_buyer_by_phone(self, phone: str) -> dict | None:
        """`{"full_name": ..., "email": ...}` or None if there is nothing to say."""
        ...
