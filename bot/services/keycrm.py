"""KeyCRM REST API client for order lookup by phone number."""
from __future__ import annotations

from dataclasses import dataclass, field

import httpx
from loguru import logger

BASE_URL = "https://openapi.keycrm.app/v1"


@dataclass
class KeyCRMOrder:
    """Typed representation of a KeyCRM order."""

    id: int
    status_name: str
    grand_total: float
    ordered_at: str
    products: list[dict] = field(default_factory=list)
    buyer_name: str = ""
    payment_status: str = ""


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
    products = [
        {"name": p["name"], "qty": p["quantity"]}
        for p in raw.get("products", [])
    ]
    status_name = raw.get("status", {}).get("name", "unknown")
    buyer_name = raw.get("buyer", {}).get("full_name", "")

    return KeyCRMOrder(
        id=raw["id"],
        status_name=status_name,
        grand_total=float(raw.get("grand_total", 0)),
        ordered_at=raw.get("created_at", ""),
        products=products,
        buyer_name=buyer_name,
        payment_status=raw.get("payment_status", ""),
    )


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
            "include": "buyer,products,status",
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
