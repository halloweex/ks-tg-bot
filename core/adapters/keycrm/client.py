"""KeyCRM REST API client for order lookup by phone number.

Transport only. What a response means lives in parse.py, so this file is the
part that genuinely needs a network to test, and it is now the only part.
"""
from __future__ import annotations

import asyncio

import httpx
from loguru import logger

from core.adapters.keycrm.parse import (KeyCRMOrder, last_page,
                                        normalize_phone_for_keycrm,
                                        parse_buyer, parse_orders,
                                        parse_stock_page)

BASE_URL = "https://openapi.keycrm.app/v1"

# KeyCRM allows 120 requests/minute; this keeps a full stock sweep under it.
_STOCK_PAGE_PAUSE = 0.55


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
                return parse_orders(response.json())

        except httpx.HTTPError as exc:
            logger.error("KeyCRM HTTP error for phone {}: {}", normalized, exc)
            return []

    async def get_stock(self) -> dict[str, int]:
        """Whole-catalogue availability: sku -> units free to sell.

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
                    levels.update(parse_stock_page(body))
                    if page >= last_page(body):
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
                return parse_buyer(response.json())
        except httpx.HTTPError as exc:
            logger.error("KeyCRM buyer lookup error for {}: {}", normalized, exc)
            return None
