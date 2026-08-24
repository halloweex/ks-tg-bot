"""Somewhere the shop's availability can be read.

One method, because that is all the restock watcher needs and a port that
promises more than its one caller uses is a port nobody can implement twice.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from core.domain.offer import Offer


@runtime_checkable
class StockLevels(Protocol):
    """Whole-catalogue availability."""

    async def get_stock(self) -> dict[str, int]:
        """sku -> units free to sell.

        Empty means "could not read", not "nothing is in stock": a partial
        catalogue would look exactly like every missing sku having sold out, and
        the next poll would then read the recovery as a restock of all of them.
        The adapter is responsible for returning {} rather than a half picture.
        """
        ...


@runtime_checkable
class Storefront(Protocol):
    """What the shop sells, keyed by the sku the rest of the system speaks.

    Separate from StockLevels above even though both answer a question about
    availability, because they answer different ones and are read by different
    callers. StockLevels is the CRM's unit count, polled every fifteen minutes
    to catch a restock. This is the storefront's own "can it be bought" plus the
    variant id a cart link needs, and only a screen that offers to buy asks for
    it. One port promising both would force every implementer to have both.
    """

    async def get_offers(self) -> dict[str, Offer]:
        """sku -> offer, for everything currently sellable.

        Empty means "could not read", for the same reason spelled out above: a
        half-read catalogue would silently hide the buy button on whatever the
        failed pages happened to hold.
        """
        ...
