"""Shopify response shapes: pure functions over decoded JSON, no transport.

Same split as the KeyCRM adapter, for the same reason — everything here runs on
a saved fixture. Note that these fixtures are reconstructions, not recordings:
no Shopify credentials are configured (docs/found-during-move.md §6), so they
cannot catch a field the store sends that nobody thought to include.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from core.domain.order import shopify_external_id


@dataclass
class ShopifyOrder:
    """Typed representation of a Shopify order."""

    id: str
    name: str
    financial_status: str
    fulfillment_status: str
    total_price: str
    currency: str
    created_at: str
    line_items: list[dict] = field(default_factory=list)


def _parse_shopify_order(node: dict) -> ShopifyOrder:
    """Parse a raw Shopify GraphQL order node into a typed ShopifyOrder dataclass."""
    price_set = node.get("totalPriceSet", {}).get("shopMoney", {})
    total_price = price_set.get("amount", "0")
    currency = price_set.get("currencyCode", "")

    line_items = [
        {"name": e["node"]["name"], "qty": e["node"]["quantity"]}
        for e in node.get("lineItems", {}).get("edges", [])
    ]

    return ShopifyOrder(
        id=node["id"],
        name=node.get("name", ""),
        financial_status=node.get("displayFinancialStatus", ""),
        fulfillment_status=node.get("displayFulfillmentStatus", ""),
        total_price=total_price,
        currency=currency,
        created_at=node.get("createdAt", ""),
        line_items=line_items,
    )


def parse_orders(body: dict) -> list[ShopifyOrder]:
    """Orders of the first matching customer; empty list if there is no match.

    Call this only after the caller has checked for a GraphQL `errors` block: a
    failed query answers with `data: null`, and the walk below would step into
    the None. The client checks first, which is what keeps that unreachable.
    """
    customers_edges = body.get("data", {}).get("customers", {}).get("edges", [])
    if not customers_edges:
        return []

    customer_node = customers_edges[0]["node"]
    order_edges = customer_node.get("orders", {}).get("edges", [])
    return [_parse_shopify_order(e["node"]) for e in order_edges]


def shopify_order_to_dict(order: ShopifyOrder, chat_id: int) -> dict:
    """Convert a ShopifyOrder dataclass to a dict for upsert_orders()."""
    return {
        "chat_id": chat_id,
        "source": "shopify",
        "source_order_id": order.id,
        "external_id": shopify_external_id(order.id),
        "order_name": order.name,
        "status_name": order.fulfillment_status or order.financial_status or "",
        "status_group_id": 0,   # not a KeyCRM order, so it has no status group
        "grand_total": float(order.total_price),
        "currency": order.currency,
        "ordered_at": order.created_at,
        "products_json": json.dumps(order.line_items, ensure_ascii=False),
        "buyer_name": "",
        "payment_status": order.financial_status,
        "tracking_code": "",
        "shipping_status": order.fulfillment_status,
        "delivery_city": "",
        "receive_point": "",
        "recipient_name": "",
    }
