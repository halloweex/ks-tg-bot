"""Shopify response shapes: pure functions over decoded JSON, no transport.

Same split as the KeyCRM adapter, for the same reason — everything here runs on
a saved fixture. Note that these fixtures are reconstructions, not recordings:
no Shopify credentials are configured (docs/found-during-move.md §6), so they
cannot catch a field the store sends that nobody thought to include.
"""
from __future__ import annotations

from core.domain.order import Order, shopify_external_id


def parse_shopify_order(node: dict) -> Order:
    """One raw Shopify GraphQL order node into the domain's Order.

    Shopify keeps two statuses where the rest of the system has one: an order is
    paid or not, and fulfilled or not. `status_name` takes fulfilment first
    because that is the one a customer is waiting on, and falls back to payment
    for an order that has not shipped yet — both are kept in their own fields
    besides, so nothing is lost by choosing.
    """
    price_set = node.get("totalPriceSet", {}).get("shopMoney", {})
    total_price = price_set.get("amount", "0")
    currency = price_set.get("currencyCode", "")

    line_items = [
        {"name": e["node"]["name"], "qty": e["node"]["quantity"]}
        for e in node.get("lineItems", {}).get("edges", [])
    ]

    financial_status = node.get("displayFinancialStatus", "")
    fulfillment_status = node.get("displayFulfillmentStatus", "")
    gid = node["id"]

    return Order(
        source="shopify",
        # The whole gid, not its tail: it is this system's own id for the order,
        # and the numeric tail is what the two systems share (external_id).
        source_order_id=gid,
        external_id=shopify_external_id(gid),
        order_name=node.get("name", ""),
        status_name=fulfillment_status or financial_status or "",
        status_group_id=0,   # not a KeyCRM order, so it has no status group
        grand_total=float(total_price),
        currency=currency,
        ordered_at=node.get("createdAt", ""),
        items=line_items,
        payment_status=financial_status,
        shipping_status=fulfillment_status,
    )


def parse_orders(body: dict) -> list[Order]:
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
    return [parse_shopify_order(e["node"]) for e in order_edges]


# shopify_order_to_dict lived here and is gone, for the same reason its KeyCRM
# twin did: the cache row is core.domain.order_row now, one function instead of
# two that had to be kept in step by hand.
