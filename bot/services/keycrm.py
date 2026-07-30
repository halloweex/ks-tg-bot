"""KeyCRM REST API client for order lookup by phone number."""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field

import httpx
from loguru import logger

BASE_URL = "https://openapi.keycrm.app/v1"

# KeyCRM allows 120 requests/minute; this keeps a full stock sweep under it.
_STOCK_PAGE_PAUSE = 0.55


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
    delivery_city = shipping.get("delivery_city", "") or ""
    receive_point = shipping.get("receive_point", "") or ""
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


class KeyCRMClient:
    """Async client for the KeyCRM REST API."""

    def __init__(self, api_key: str) -> None:
        self._headers = {
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
        }

    async def get_orders_by_phone(self, phone: str) -> list[KeyCRMOrder]:
        """Look up orders by buyer phone number.

        Phone is normalized before querying: '+' prefix and formatting chars are stripped.
        KeyCRM filter[buyer_phone] does exact match, so normalization is critical.

        Returns empty list on any HTTP error (never raises).
        """
        normalized = normalize_phone_for_keycrm(phone)
        params = {
            "include": "buyer,products,status,shipping",
            "filter[buyer_phone]": normalized,
            "limit": 50,
        }

        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{BASE_URL}/order",
                    headers=self._headers,
                    params=params,
                    timeout=10.0,
                )
                if response.status_code == 429:
                    logger.warning("KeyCRM rate limit hit (429) for phone {}", normalized)
                response.raise_for_status()
                data = response.json()
                return [_parse_order(order) for order in data.get("data", [])]

        except httpx.HTTPError as exc:
            logger.error("KeyCRM HTTP error for phone {}: {}", normalized, exc)
            return []

    async def get_stock(self) -> dict[str, int]:
        """Whole-catalogue availability: sku -> units free to sell.

        Availability is quantity minus reserve — stock already promised to open
        orders is not something a waiting customer can buy.

        ~860 skus over 18 pages, paced under the 120 req/min limit, so a full
        snapshot takes roughly 15 seconds. Returns {} on error rather than a
        partial picture: a half-read catalogue would look like a restock for
        every sku it failed to fetch.
        """
        levels: dict[str, int] = {}
        try:
            async with httpx.AsyncClient(headers=self._headers, timeout=30.0) as client:
                page = 1
                while True:
                    response = await client.get(
                        f"{BASE_URL}/offers/stocks",
                        params={"limit": 50, "page": page},
                    )
                    response.raise_for_status()
                    body = response.json()
                    for offer in body.get("data", []):
                        sku = str(offer.get("sku") or "").strip()
                        if not sku:
                            continue
                        quantity = int(offer.get("quantity") or 0)
                        reserve = int(offer.get("reserve") or 0)
                        levels[sku] = quantity - reserve
                    if page >= int(body.get("last_page") or 1):
                        break
                    page += 1
                    await asyncio.sleep(_STOCK_PAGE_PAUSE)
        except (httpx.HTTPError, ValueError) as exc:
            logger.error("KeyCRM stock fetch failed on page {}: {}", page, exc)
            return {}
        return levels

    async def get_buyer_by_phone(self, phone: str) -> dict | None:
        """Fetch buyer profile (full_name, email) by phone from the first order.

        Returns None if no orders or on error.
        """
        normalized = normalize_phone_for_keycrm(phone)
        params = {
            "include": "buyer",
            "filter[buyer_phone]": normalized,
            "limit": 1,
        }
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{BASE_URL}/order",
                    headers=self._headers,
                    params=params,
                    timeout=10.0,
                )
                response.raise_for_status()
                data = response.json()
                orders = data.get("data", [])
                if not orders:
                    return None
                buyer = orders[0].get("buyer") or {}
                full_name = buyer.get("full_name", "")
                email = buyer.get("email", "")
                if not full_name and not email:
                    return None
                return {"full_name": full_name, "email": email}
        except httpx.HTTPError as exc:
            logger.error("KeyCRM buyer lookup error for {}: {}", normalized, exc)
            return None
