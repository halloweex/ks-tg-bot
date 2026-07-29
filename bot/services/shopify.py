"""Shopify GraphQL Admin API client for order lookup by phone number."""
from __future__ import annotations

import json
from dataclasses import dataclass, field

import httpx
from loguru import logger

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


@dataclass
class ShopifyOrder:
    """Typed representation of a Shopify order."""

    id: str
    name: str
    financial_status: str
    fulfillment_status: str
    total_price: str
    currency: str
    created_at: str
    line_items: list[dict] = field(default_factory=list)


def _parse_shopify_order(node: dict) -> ShopifyOrder:
    """Parse a raw Shopify GraphQL order node into a typed ShopifyOrder dataclass."""
    price_set = node.get("totalPriceSet", {}).get("shopMoney", {})
    total_price = price_set.get("amount", "0")
    currency = price_set.get("currencyCode", "")

    line_items = [
        {"name": e["node"]["name"], "qty": e["node"]["quantity"]}
        for e in node.get("lineItems", {}).get("edges", [])
    ]

    return ShopifyOrder(
        id=node["id"],
        name=node.get("name", ""),
        financial_status=node.get("displayFinancialStatus", ""),
        fulfillment_status=node.get("displayFulfillmentStatus", ""),
        total_price=total_price,
        currency=currency,
        created_at=node.get("createdAt", ""),
        line_items=line_items,
    )


def shopify_external_id(gid: str) -> str:
    """Numeric order id from a Shopify GraphQL gid.

    'gid://shopify/Order/13025577828684' -> '13025577828684'. This is the value
    KeyCRM stores as `global_source_uuid` on orders it pulled in through the
    Shopify integration, so it is the key that matches the two systems' copies
    of one order. Returns '' if the gid has no numeric tail.
    """
    tail = gid.rsplit("/", 1)[-1] if gid else ""
    return tail if tail.isdigit() else ""


def shopify_order_to_dict(order: ShopifyOrder, chat_id: int) -> dict:
    """Convert a ShopifyOrder dataclass to a dict for upsert_orders()."""
    return {
        "chat_id": chat_id,
        "source": "shopify",
        "source_order_id": order.id,
        "external_id": shopify_external_id(order.id),
        "order_name": order.name,
        "status_name": order.fulfillment_status or order.financial_status or "",
        "grand_total": float(order.total_price),
        "currency": order.currency,
        "ordered_at": order.created_at,
        "products_json": json.dumps(order.line_items, ensure_ascii=False),
        "buyer_name": "",
        "payment_status": order.financial_status,
        "tracking_code": "",
        "shipping_status": order.fulfillment_status,
        "delivery_city": "",
        "receive_point": "",
        "recipient_name": "",
    }


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

    async def get_orders_by_phone(self, phone: str) -> list[ShopifyOrder]:
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

                if "errors" in data:
                    logger.error("Shopify GraphQL errors for phone {}: {}", phone, data["errors"])
                    return []

                customers_edges = data.get("data", {}).get("customers", {}).get("edges", [])
                if not customers_edges:
                    return []

                customer_node = customers_edges[0]["node"]
                order_edges = customer_node.get("orders", {}).get("edges", [])
                return [_parse_shopify_order(e["node"]) for e in order_edges]

        except httpx.HTTPError as exc:
            logger.error("Shopify HTTP error for phone {}: {}", phone, exc)
            return []
