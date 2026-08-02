"""What a scenario needs from a shop system, expressed without naming one.

Structural, not inherited: these are Protocols, so KeyCRMClient and ShopifyClient
satisfy them by having the right methods and never import this module. That is
deliberate — an adapter that has to inherit from a port ends up knowing about
the layer above it, and the direction of the dependency is the only thing this
package exists to control.

The two are separate because the two sources are not equal. Both answer with
orders; only KeyCRM knows who the buyer is, and a `BuyerLookup` that Shopify
could not implement would be a lie in the type.
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
class BuyerLookup(Protocol):
    """Somewhere a customer's profile can be read from by phone number."""

    async def get_buyer_by_phone(self, phone: str) -> dict | None:
        """`{"full_name": ..., "email": ...}` or None if there is nothing to say."""
        ...
