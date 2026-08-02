"""Order identity: one rule for deciding that two rows are the same order.

Pure, no imports from the rest of the bot — this is the piece docs/components.md
puts in core.domain, and keeping it dependency-free is what makes that move a
rename rather than a rewrite.
"""
from __future__ import annotations

from typing import Final, Mapping

# Which source wins when both describe the same order. KeyCRM is the operational
# system of record — fulfilment status, tracking code, delivery point — and it
# carries the store order number too, so nothing is lost by preferring it.
SOURCE_RANK: Final[Mapping[str, int]] = {"keycrm": 2, "shopify": 1, "demo": 0}


def source_rank(source: str) -> int:
    return SOURCE_RANK.get(source, 0)


def merge_key(source: str, source_order_id: str, external_id: str | None) -> str:
    """Stable identity of one physical order, whichever system reported it.

    An order that exists in the store carries the store's numeric id — Shopify's
    own, which KeyCRM mirrors as `global_source_uuid` — so both systems' copies
    land on the same key. An order that exists in one system only falls back to
    that system's id.

    The prefix is not optional on either branch. A bare number would collide the
    day a second channel starts reporting numeric ids, and the collision would
    surface as an order silently overwriting an unrelated one — the hardest
    possible symptom to trace back to here.
    """
    if external_id:
        return f"shopify:{external_id}"
    return f"{source}:{source_order_id}"


def shopify_external_id(gid: str) -> str:
    """Numeric order id from a Shopify GraphQL gid.

    'gid://shopify/Order/13025577828684' -> '13025577828684'. This is the value
    KeyCRM stores as `global_source_uuid` on orders it pulled in through the
    Shopify integration, so it is the key that matches the two systems' copies
    of one order. Returns '' if the gid has no numeric tail.

    Lives next to merge_key because it produces the `external_id` merge_key
    branches on: one rule for order identity, in one place.
    """
    tail = gid.rsplit("/", 1)[-1] if gid else ""
    return tail if tail.isdigit() else ""
