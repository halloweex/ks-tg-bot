"""Somewhere the shop's availability can be read.

One method, because that is all the restock watcher needs and a port that
promises more than its one caller uses is a port nobody can implement twice.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable


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
