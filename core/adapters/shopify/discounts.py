"""Creating a promo code in Shopify, one at a time.

The only write this system makes to the shop. Everything else it does with
Shopify is reading: the public product feed, and order lookup by phone.

`discountCodeBasicCreate` is the mutation, and the input shape below was read
off the store's own schema rather than the documentation: in this API version
`DiscountCodeBasicInput` has no `customerSelection` field, and sending one
fails the whole call. A code with no customer selection is offered to everyone,
which is what a single-use code handed to one person already amounts to.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timezone

import httpx
from loguru import logger

_MUTATION = """
mutation Create($discount: DiscountCodeBasicInput!) {
  discountCodeBasicCreate(basicCodeDiscount: $discount) {
    codeDiscountNode { id }
    userErrors { field message }
  }
}
"""

# No 0/O, no 1/I/L: a code is read off a screen and typed on a phone, sometimes
# by the friend it was forwarded to. Twenty-eight letters and digits over eight
# characters is more combinations than the shop will ever issue, so a collision
# is not worth a retry loop — Shopify refuses a duplicate and the caller falls
# back to the manual path, same as any other failure.
_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
_LENGTH = 8
_TIMEOUT = 10.0


class ShopifyDiscounts:
    """Implements the DiscountCodes port over the GraphQL Admin API.

    Needs a token with `write_discounts`, and nothing else. The token the shop
    already uses for its CRM integration carries a hundred more scopes than
    that — including rights over themes, orders and payouts — and is the wrong
    one to put in a bot.
    """

    def __init__(self, store_url: str, api_token: str, prefix: str = "KS") -> None:
        self._endpoint = f"https://{store_url}/admin/api/2025-01/graphql.json"
        self._headers = {
            "X-Shopify-Access-Token": api_token,
            "Content-Type": "application/json",
        }
        self._prefix = prefix

    def _new_code(self) -> str:
        body = "".join(secrets.choice(_ALPHABET) for _ in range(_LENGTH))
        return f"{self._prefix}-{body}"

    async def issue_percentage(self, percent: int, *, title: str) -> str | None:
        """Create a one-use code for `percent` off, or None if anything failed.

        Never raises. The caller's fallback is the manual path that existed
        before this port, and reaching it is a slower kept promise; an
        exception here would instead lose the reward mid-sweep.
        """
        if percent <= 0:
            return None

        code = self._new_code()
        variables = {"discount": {
            "title": title,
            "code": code,
            "startsAt": datetime.now(timezone.utc).isoformat(),
            # One customer, one use. Both, because they answer different
            # questions: the limit stops the code being passed around, and
            # appliesOncePerCustomer stops the same person spending it twice
            # in one order flow.
            "usageLimit": 1,
            "appliesOncePerCustomer": True,
            # Shopify wants a fraction, not a percentage: 0.1 is ten per cent.
            "customerGets": {
                "value": {"percentage": percent / 100},
                "items": {"all": True},
            },
        }}

        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                response = await client.post(
                    self._endpoint, headers=self._headers,
                    json={"query": _MUTATION, "variables": variables},
                )
                response.raise_for_status()
                data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            logger.error("Shopify discount create failed: {}", exc)
            return None

        # A failed GraphQL call answers 200 with an errors array, and a
        # rejected input answers 200 with userErrors. Both are failures here.
        if data.get("errors"):
            logger.error("Shopify discount GraphQL errors: {}", data["errors"])
            return None
        result = (data.get("data") or {}).get("discountCodeBasicCreate") or {}
        if result.get("userErrors"):
            logger.error("Shopify refused the discount: {}", result["userErrors"])
            return None
        if not (result.get("codeDiscountNode") or {}).get("id"):
            logger.error("Shopify returned no discount node: {}", data)
            return None

        logger.info("Issued discount code {} ({}%)", code, percent)
        return code
