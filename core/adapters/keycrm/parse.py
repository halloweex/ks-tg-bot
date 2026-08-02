"""KeyCRM response shapes: pure functions over decoded JSON, no transport.

Everything here runs on a saved fixture. That is the whole point of splitting
the file: until now unpacking the envelope happened inside
`async with httpx.AsyncClient`, so the only way to reach it from a test was a
mock transport, and the parts nobody mocked stayed uncovered.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field


@dataclass
class KeyCRMOrder:
    """Typed representation of a KeyCRM order."""

    id: int
    status_name: str
    # KeyCRM groups statuses; group 6 is the cancelled/returned/unavailable
    # family. Filtering on that is stable, while status names can be renamed in
    # the CRM at any time.
    status_group_id: int
    grand_total: float
    ordered_at: str
    # Identity of the same order in the upstream store, for orders KeyCRM pulled
    # in through an integration. For the Shopify source these are, respectively,
    # the Shopify numeric order id (matches the tail of the GraphQL gid) and the
    # human order number ('19966' -> Shopify calls the order '#19966').
    # Both are null for manually created orders (Instagram, Telegram, expo).
    external_id: str = ""
    external_number: str = ""
    products: list[dict] = field(default_factory=list)
    buyer_name: str = ""
    buyer_email: str = ""
    payment_status: str = ""
    tracking_code: str = ""
    shipping_status: str = ""
    delivery_city: str = ""
    receive_point: str = ""
    recipient_name: str = ""


def normalize_phone_for_keycrm(phone: str) -> str:
    """Strip +, spaces, dashes, parens. '+380671234567' -> '380671234567'"""
    return (
        phone.replace("+", "")
        .replace(" ", "")
        .replace("-", "")
        .replace("(", "")
        .replace(")", "")
    )


def _parse_order(raw: dict) -> KeyCRMOrder:
    """Parse a raw KeyCRM order dict into a typed KeyCRMOrder dataclass."""
    # sku is kept so favourites group by the product itself: names get edited in
    # the CRM, and grouping on them would split one product into several.
    products = [
        {"name": p["name"], "qty": p["quantity"], "sku": p.get("sku") or ""}
        for p in raw.get("products", [])
    ]
    status_name = raw.get("status", {}).get("name", "unknown")
    status_group_id = int(raw.get("status_group_id") or 0)
    buyer = raw.get("buyer") or {}
    buyer_name = buyer.get("full_name", "")
    buyer_email = buyer.get("email", "")

    shipping = raw.get("shipping") or {}
    tracking_code = shipping.get("tracking_code", "") or ""
    shipping_status = shipping.get("shipping_status", "") or ""
    # KeyCRM names these shipping_address_city / shipping_receive_point. Reading
    # "delivery_city" / "receive_point" — fields the API does not have — is why
    # the delivery view looked empty for every order. The bare names are kept as
    # a fallback in case older records ever carried them.
    delivery_city = (shipping.get("shipping_address_city")
                     or shipping.get("delivery_city") or "")
    receive_point = (shipping.get("shipping_receive_point")
                     or shipping.get("receive_point") or "")
    recipient_name = shipping.get("recipient_full_name", "") or ""

    return KeyCRMOrder(
        id=raw["id"],
        status_name=status_name,
        status_group_id=status_group_id,
        grand_total=float(raw.get("grand_total", 0)),
        ordered_at=raw.get("created_at", ""),
        external_id=str(raw.get("global_source_uuid") or ""),
        external_number=str(raw.get("source_uuid") or ""),
        products=products,
        buyer_name=buyer_name,
        buyer_email=buyer_email,
        payment_status=raw.get("payment_status", ""),
        tracking_code=tracking_code,
        shipping_status=shipping_status,
        delivery_city=delivery_city,
        receive_point=receive_point,
        recipient_name=recipient_name,
    )


def parse_orders(body: dict) -> list[KeyCRMOrder]:
    """Orders out of a list response.

    `last_page`, `total` and `next_page_url` sit in the same envelope and are
    still ignored — see docs/found-during-move.md §4. Reading them is a change
    in behaviour, so it does not happen in a move; this is where it will happen.
    """
    return [_parse_order(order) for order in body.get("data", [])]


def parse_buyer(body: dict) -> dict | None:
    """Buyer profile off the first order of a list response, None if unusable."""
    orders = body.get("data", [])
    if not orders:
        return None
    buyer = orders[0].get("buyer") or {}
    full_name = buyer.get("full_name", "")
    email = buyer.get("email", "")
    if not full_name and not email:
        return None
    return {"full_name": full_name, "email": email}


def parse_stock_page(body: dict) -> dict[str, int]:
    """One page of /offers/stocks as sku -> units free to sell.

    Availability is quantity minus reserve — stock already promised to open
    orders is not something a waiting customer can buy. Offers without a sku are
    skipped: the sku is the only key the rest of the system knows a product by.
    """
    levels: dict[str, int] = {}
    for offer in body.get("data", []):
        sku = str(offer.get("sku") or "").strip()
        if not sku:
            continue
        quantity = int(offer.get("quantity") or 0)
        reserve = int(offer.get("reserve") or 0)
        levels[sku] = quantity - reserve
    return levels


def last_page(body: dict) -> int:
    """Page count from a paginated envelope; 1 when the field is missing."""
    return int(body.get("last_page") or 1)


def keycrm_order_to_dict(order: KeyCRMOrder, chat_id: int) -> dict:
    """Convert a KeyCRMOrder dataclass to a dict for upsert_orders().

    Orders that came from the Shopify integration carry the store's order
    number, so they render as web orders ('🌐 Сайт #19966') rather than falling
    back to the Instagram label — and their external_id lets the merge step drop
    the Shopify copy of the same order.
    """
    return {
        "chat_id": chat_id,
        "source": "keycrm",
        "source_order_id": str(order.id),
        "external_id": order.external_id,
        "order_name": f"#{order.external_number}" if order.external_number else "",
        "status_name": order.status_name,
        "status_group_id": order.status_group_id,
        "grand_total": order.grand_total,
        "currency": "грн",
        "ordered_at": order.ordered_at,
        "products_json": json.dumps(order.products, ensure_ascii=False),
        "buyer_name": order.buyer_name,
        "payment_status": order.payment_status,
        "tracking_code": order.tracking_code,
        "shipping_status": order.shipping_status,
        "delivery_city": order.delivery_city,
        "receive_point": order.receive_point,
        "recipient_name": order.recipient_name,
    }
