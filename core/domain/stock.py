"""When a product counts as having come back.

A restock is a transition, not a state: a sku that had nothing free to sell now
has some. Only transitions are worth telling anyone about — a product that
simply stays in stock would otherwise fire on every poll, and the very first
poll would fire for the whole catalogue at once.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Waiting:
    """One standing promise: tell this chat when this sku is back.

    The product name travels with the row rather than being looked up when the
    message is written. It is the name as it read when the person subscribed,
    and carrying it is what lets a restock be announced without a catalogue
    call — the sweep already refuses to act on an empty catalogue read, and a
    notification needing a second network answer could fail the same way after
    the subscription has already been released.
    """

    chat_id: int
    sku: str
    name: str


def restocked(previous: dict[str, int], current: dict[str, int]) -> list[str]:
    """Skus that went from nothing available to something.

    A sku absent from `previous` is new to us and never counts. On the first
    poll that rule is the difference between one quiet baseline and eight
    hundred notifications; afterwards it is what keeps a product added to the
    catalogue mid-week from looking like a return.
    """
    return [
        sku for sku, available in current.items()
        if available > 0 and previous.get(sku, 1) <= 0 and sku in previous
    ]
