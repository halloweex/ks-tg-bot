"""Shopify response shapes: pure functions over decoded JSON, no transport.

Same split as the KeyCRM adapter, for the same reason — everything here runs on
a saved fixture. Note that these fixtures are reconstructions, not recordings:
no Shopify credentials are configured (docs/found-during-move.md §6), so they
cannot catch a field the store sends that nobody thought to include.
"""
from __future__ import annotations

from core.domain.offer import Offer
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


def _image_for(product: dict, variant: dict) -> str:
    """The one picture to show for this variant, or "" if the feed has none.

    Shopify resolves the variant-to-image link itself: a variant with its own
    photo carries it as `featured_image`, and this store leaves that null on
    everything — one photo per product, no per-shade shots. So the product's
    first image is the answer in practice, and the variant's own is honoured
    where it exists rather than being second-guessed from `images[].variant_ids`.
    """
    featured = variant.get("featured_image") or {}
    src = str(featured.get("src") or "")
    if src:
        return src
    images = product.get("images") or []
    return str(images[0].get("src") or "") if images else ""


def parse_offers_page(body: dict) -> dict[str, Offer]:
    """One page of the storefront's public product list into offers by sku.

    Variants without a sku are skipped rather than keyed by something invented:
    the sku is the only join to the order history, and an offer nothing can
    match to is an offer no screen will ever show.

    A sku appearing twice — the same product listed in two variants, which this
    catalogue does have — keeps whichever copy can actually be bought. Taking
    the last one blindly would hide a buyable product behind a sold-out twin.
    """
    offers: dict[str, Offer] = {}
    for product in body.get("products", []):
        handle = str(product.get("handle") or "")
        title = str(product.get("title") or "")
        for variant in product.get("variants", []):
            sku = str(variant.get("sku") or "").strip()
            if not sku:
                continue
            try:
                variant_id = int(variant.get("id"))
            except (TypeError, ValueError):
                continue
            offer = Offer(
                sku=sku,
                variant_id=variant_id,
                handle=handle,
                title=title,
                price=str(variant.get("price") or ""),
                available=bool(variant.get("available")),
                image_url=_image_for(product, variant),
            )
            existing = offers.get(sku)
            if existing is None or (offer.available and not existing.available):
                offers[sku] = offer
    return offers
