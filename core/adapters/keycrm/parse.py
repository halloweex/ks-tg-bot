"""KeyCRM response shapes: pure functions over decoded JSON, no transport.

Everything here runs on a saved fixture. That is the whole point of splitting
the file: until now unpacking the envelope happened inside
`async with httpx.AsyncClient`, so the only way to reach it from a test was a
mock transport, and the parts nobody mocked stayed uncovered.
"""
from __future__ import annotations

from core.domain.order import Order

# The currency KeyCRM orders are in. The API does not send one — every order in
# this account is in hryvnia — so the adapter supplies it rather than leaving
# the cache to guess.
_CURRENCY = "грн"


def normalize_phone_for_keycrm(phone: str) -> str:
    """Strip +, spaces, dashes, parens. '+380671234567' -> '380671234567'"""
    return (
        phone.replace("+", "")
        .replace(" ", "")
        .replace("-", "")
        .replace("(", "")
        .replace(")", "")
    )


def parse_order(raw: dict) -> Order:
    """One raw KeyCRM order into the domain's Order.

    This is where KeyCRM's vocabulary stops. `status_group_id` keeps its name
    because the business uses it — group 6 is the cancelled/returned family, and
    filtering on it is stable while status names can be renamed in the CRM at
    any time — but `global_source_uuid` becomes `external_id` and `source_uuid`
    becomes the order name, because that is what they mean.
    """
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

    # The human order number, and only for orders KeyCRM pulled in through an
    # integration: '19966' is what Shopify calls '#19966'. Null for anything
    # created by hand (Instagram, Telegram, expo), which then renders under its
    # own label rather than as a web order.
    external_number = str(raw.get("source_uuid") or "")

    return Order(
        source="keycrm",
        source_order_id=str(raw["id"]),
        # The same physical order in the upstream store. Matches the tail of the
        # Shopify gid, which is what lets the two systems' copies merge.
        external_id=str(raw.get("global_source_uuid") or ""),
        order_name=f"#{external_number}" if external_number else "",
        status_name=status_name,
        status_group_id=status_group_id,
        grand_total=float(raw.get("grand_total", 0)),
        currency=_CURRENCY,
        ordered_at=raw.get("created_at", ""),
        items=products,
        buyer_name=buyer_name,
        buyer_email=buyer_email,
        payment_status=raw.get("payment_status", ""),
        tracking_code=tracking_code,
        shipping_status=shipping_status,
        delivery_city=delivery_city,
        receive_point=receive_point,
        recipient_name=recipient_name,
    )


def parse_orders(body: dict) -> list[Order]:
    """Orders out of one page of a list response.

    The envelope's `last_page` is read by the client, which pages until it runs
    out — see docs/found-during-move.md §4 for the day it did not.
    """
    return [parse_order(order) for order in body.get("data", [])]


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


def retry_after_seconds(header: str | None) -> float | None:
    """The Retry-After header as seconds, or None if it does not say.

    Only the delta-seconds form is read. RFC 9110 also allows an HTTP date, and
    honouring it would mean parsing a date, trusting the client's clock and
    handling a value in the past — for a header KeyCRM sends as a plain number.
    An unreadable value returns None, and the caller backs off on its own
    schedule rather than guessing.
    """
    if header is None:
        return None
    try:
        seconds = float(header.strip())
    except (AttributeError, ValueError):
        return None
    return seconds if seconds >= 0 else None


# keycrm_order_to_dict lived here and is gone: turning an order into a cache row
# is the same job whichever system reported it, so it is core.domain.order_row.
