"""The storefront's public product list, read without credentials.

Shopify serves /products.json to anyone, which is the whole reason this exists:
the Admin API would need a token nobody has configured (docs/found-during-move
§6), and everything the buy button needs — variant id, price, whether it can be
bought — is in the public feed already.

What it is not: a mirror of the catalogue. Nothing here is authoritative about
stock levels, which the CRM answers for, and nothing here is shown to a customer
who has not already bought the product.
"""
from __future__ import annotations

import asyncio

import httpx
from loguru import logger

from core.adapters.shopify.parse import parse_offers_page
from core.domain.offer import Offer

# 250 is the maximum the endpoint honours; the catalogue is ~625 skus over three
# pages. The cap on pages is a guard against a feed that never returns an empty
# page, not an expected limit — at 250 a page it allows for eight times the
# current catalogue.
_PAGE_SIZE = 250
_MAX_PAGES = 20
_PAGE_PAUSE = 0.3


class ShopifyStorefront:
    """Reads the shop's public product feed. Implements the Storefront port."""

    def __init__(self, website_url: str) -> None:
        self._url = f"{website_url.rstrip('/')}/products.json"

    async def get_offers(self) -> dict[str, Offer]:
        """Every sellable variant with a sku, or {} if the read failed.

        Partial results are thrown away deliberately — see the port. A page that
        errors out halfway would otherwise take the buy button off whatever
        products happened to sit on the pages after it.
        """
        offers: dict[str, Offer] = {}
        page = 1
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                while page <= _MAX_PAGES:
                    response = await client.get(
                        self._url, params={"limit": _PAGE_SIZE, "page": page}
                    )
                    response.raise_for_status()
                    body = response.json()
                    if not body.get("products"):
                        break
                    offers.update(parse_offers_page(body))
                    page += 1
                    await asyncio.sleep(_PAGE_PAUSE)
        except (httpx.HTTPError, ValueError) as exc:
            logger.error("Storefront catalogue fetch failed on page {}: {}", page, exc)
            return {}
        logger.info("Storefront catalogue: {} offers over {} pages", len(offers), page - 1)
        return offers
