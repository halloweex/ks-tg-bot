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
                                        parse_stock_page, retry_after_seconds)

BASE_URL = "https://openapi.keycrm.app/v1"

# KeyCRM allows 120 requests/minute; this keeps a paged sweep under it.
_PAGE_PAUSE = 0.55

# How many pages of orders one phone is allowed to cost. The heaviest customer
# in the CRM has 140 orders — three pages — so this is not a limit anyone reaches
# by shopping; it is a stop for a wholesale account, measured against a screen
# the customer is waiting in front of. Hitting it is logged, because an order
# list silently missing its tail is exactly the defect this replaced.
_MAX_ORDER_PAGES = 20

# Retries for 429 only. Everything else is either fine or not our problem.
_MAX_ATTEMPTS = 3
# A customer is looking at a loading screen, so an honest Retry-After of two
# minutes is not something to obey — better to come back with what we have and
# let the next refresh fill in the rest.
_RETRY_CAP_SECONDS = 5.0


class KeyCRMClient:
    """Async client for the KeyCRM REST API."""

    def __init__(self, api_key: str) -> None:
        self._headers = {
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
        }

    async def _get(
        self, client: httpx.AsyncClient, url: str, *, params: dict
    ) -> httpx.Response:
        """GET with the one thing worth retrying: a rate limit.

        Until this existed the 429 branch wrote a line to the log and fell
        through to raise_for_status(), so the only effect of being rate-limited
        was a customer seeing no orders. Retry-After is obeyed when KeyCRM sends
        one and capped, otherwise the wait doubles from a second.
        """
        response = await client.get(url, params=params)
        for attempt in range(1, _MAX_ATTEMPTS):
            if response.status_code != 429:
                return response
            delay = retry_after_seconds(response.headers.get("Retry-After"))
            if delay is None:
                delay = float(2 ** (attempt - 1))
            delay = min(delay, _RETRY_CAP_SECONDS)
            logger.warning(
                "KeyCRM rate limit (429), waiting {}s before attempt {} of {}",
                delay, attempt + 1, _MAX_ATTEMPTS,
            )
            await asyncio.sleep(delay)
            response = await client.get(url, params=params)
        if response.status_code == 429:
            logger.error("KeyCRM still rate-limiting after {} attempts", _MAX_ATTEMPTS)
        return response

    async def get_orders_by_phone(self, phone: str) -> list[KeyCRMOrder]:
        """Every order this phone has, oldest page last.

        Phone is normalized before querying: '+' prefix and formatting chars are stripped.
        KeyCRM filter[buyer_phone] does exact match, so normalization is critical.

        Pages until the envelope says there are no more. Until it did, the
        request asked for 50 and read `last_page` never, so a customer with more
        than 50 orders quietly lost the rest — measured against the live CRM on
        2026-08-02: ten customers out of 20,218 have more than 50, the heaviest
        140. Ten people, and they are the ones who buy most.

        Never raises. On an error partway through it returns the pages it did
        get: orders are upserted, not replaced, so a short read costs freshness
        and nothing else. That is the opposite of get_stock below, where a
        partial read would look like a restock.
        """
        normalized = normalize_phone_for_keycrm(phone)
        params = {
            "include": "buyer,products,status,shipping",
            "filter[buyer_phone]": normalized,
            "limit": 50,
        }
        orders: list[KeyCRMOrder] = []

        try:
            async with httpx.AsyncClient(headers=self._headers, timeout=10.0) as client:
                page = 1
                while True:
                    response = await self._get(
                        client, f"{BASE_URL}/order", params={**params, "page": page}
                    )
                    response.raise_for_status()
                    body = response.json()
                    orders.extend(parse_orders(body))

                    pages = last_page(body)
                    if pages > _MAX_ORDER_PAGES:
                        logger.warning(
                            "KeyCRM: phone {} has {} pages of orders, reading only {}",
                            normalized, pages, _MAX_ORDER_PAGES,
                        )
                    if page >= min(pages, _MAX_ORDER_PAGES):
                        break
                    page += 1
                    await asyncio.sleep(_PAGE_PAUSE)

        except httpx.HTTPError as exc:
            logger.error("KeyCRM HTTP error for phone {}: {}", normalized, exc)

        return orders

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
                    response = await self._get(
                        client, f"{BASE_URL}/offers/stocks",
                        params={"limit": 50, "page": page},
                    )
                    response.raise_for_status()
                    body = response.json()
                    levels.update(parse_stock_page(body))
                    if page >= last_page(body):
                        break
                    page += 1
                    await asyncio.sleep(_PAGE_PAUSE)
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
            async with httpx.AsyncClient(headers=self._headers, timeout=10.0) as client:
                response = await self._get(client, f"{BASE_URL}/order", params=params)
                response.raise_for_status()
                return parse_buyer(response.json())
        except httpx.HTTPError as exc:
            logger.error("KeyCRM buyer lookup error for {}: {}", normalized, exc)
            return None
