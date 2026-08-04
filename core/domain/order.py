"""What an order is, and the one rule for deciding that two of them are the same.

Pure, no imports from the rest of the bot. `Order` is what every source reports
and what the cache stores: KeyCRM and Shopify each parse into it, so nothing
above the adapters has to know which system an order came from — that is the
whole point of it living here rather than in two adapter-shaped dataclasses.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
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


@dataclass
class Order:
    """One order as a source reported it, before it becomes a cached row.

    Every field is already in the vocabulary of this business rather than of
    whichever API delivered it: an adapter that calls a status `financial_status`
    and one that calls it `payment_status` both fill `payment_status` here, and
    the screens above stopped needing to know the difference.

    `items` stays a list of plain dicts rather than an OrderItem type, which is
    what docs/architecture.md §3 asks for. The reason is on disk: KeyCRM items
    carry a sku and Shopify items do not, and `products_json` is stored, so a
    uniform item type would rewrite the JSON of every cached Shopify order. That
    is a migration, and it belongs with one.
    """

    source: str
    source_order_id: str
    external_id: str = ""
    order_name: str = ""
    status_name: str = ""
    status_group_id: int = 0
    grand_total: float = 0.0
    currency: str = ""
    ordered_at: str = ""
    items: list[dict] = field(default_factory=list)
    buyer_name: str = ""
    buyer_email: str = ""
    # Who the order belongs to, as the source spells it. Not part of order_row
    # and deliberately so: the cache row is keyed by chat, and the number is
    # what decides *which* chat — it is read on the way in and does not survive
    # into the table. §5.4 gives orders a phone column of their own in the stage
    # that lets an order exist without a user; until then, storing it here would
    # be a second copy of an answer the users table already holds.
    buyer_phone: str = ""
    payment_status: str = ""
    tracking_code: str = ""
    shipping_status: str = ""
    delivery_city: str = ""
    receive_point: str = ""
    recipient_name: str = ""


def order_row(order: Order, chat_id: int) -> dict:
    """The order as the cache stores it, for one customer's chat.

    merge_key and source_rank are deliberately absent: they are derived by the
    repository on the way in, and letting a caller supply them is how unrelated
    orders once collapsed onto one row (see upsert_orders).
    """
    return {
        "chat_id": chat_id,
        "source": order.source,
        "source_order_id": order.source_order_id,
        "external_id": order.external_id,
        "order_name": order.order_name,
        "status_name": order.status_name,
        "status_group_id": order.status_group_id,
        "grand_total": order.grand_total,
        "currency": order.currency,
        "ordered_at": order.ordered_at,
        "products_json": json.dumps(order.items, ensure_ascii=False),
        "buyer_name": order.buyer_name,
        "payment_status": order.payment_status,
        "tracking_code": order.tracking_code,
        "shipping_status": order.shipping_status,
        "delivery_city": order.delivery_city,
        "receive_point": order.receive_point,
        "recipient_name": order.recipient_name,
    }


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
