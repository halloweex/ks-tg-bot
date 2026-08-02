"""Shopify GraphQL Admin API client for order lookup by phone number.

Transport only; what a response means lives in parse.py.
"""
from __future__ import annotations

import httpx
from loguru import logger

from core.adapters.shopify.parse import parse_orders
from core.domain.order import Order

GRAPHQL_QUERY = """
query GetCustomerByPhone($phone: String!) {
  customers(first: 1, query: $phone) {
    edges {
      node {
        id
        displayName
        orders(first: 50, sortKey: CREATED_AT, reverse: true) {
          edges {
            node {
              id
              name
              displayFinancialStatus
              displayFulfillmentStatus
              totalPriceSet {
                shopMoney { amount currencyCode }
              }
              createdAt
              lineItems(first: 10) {
                edges {
                  node { name quantity }
                }
              }
            }
          }
        }
      }
    }
  }
}
"""


class ShopifyClient:
    """Async client for the Shopify GraphQL Admin API.

    Note: If shopify_api_token is not configured, do not instantiate this class.
    The entry point checks config.env.shopify_api_token before creating the client.
    """

    def __init__(self, store_url: str, api_token: str) -> None:
        self._endpoint = f"https://{store_url}/admin/api/2025-01/graphql.json"
        self._headers = {
            "X-Shopify-Access-Token": api_token,
            "Content-Type": "application/json",
        }

    async def get_orders_by_phone(self, phone: str) -> list[Order]:
        """Look up orders for a customer by phone number.

        Phone must include the '+' prefix (opposite of KeyCRM).
        Uses Shopify query syntax: 'phone:+380671234567'.

        Returns empty list on any HTTP or GraphQL error (never raises).
        """
        variables = {"phone": f"phone:{phone}"}

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    self._endpoint,
                    headers=self._headers,
                    json={"query": GRAPHQL_QUERY, "variables": variables},
                    timeout=10.0,
                )
                response.raise_for_status()
                data = response.json()

                # Before parsing, not after: a failed GraphQL query answers 200
                # with `data: null`, and the parser walks into the data block.
                if "errors" in data:
                    logger.error("Shopify GraphQL errors for phone {}: {}", phone, data["errors"])
                    return []

                return parse_orders(data)

        except httpx.HTTPError as exc:
            logger.error("Shopify HTTP error for phone {}: {}", phone, exc)
            return []
