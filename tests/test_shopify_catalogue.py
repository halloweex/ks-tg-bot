"""The storefront catalogue: what the feed means, and what a failed read means.

The parser runs on a page saved from the live shop, so the field names are the
ones Shopify actually sends rather than the ones we hoped for. The transport
half needs a socket and goes through a mock, same as the KeyCRM client tests.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest

from core.adapters.shopify.catalog import ShopifyStorefront
from core.adapters.shopify.parse import parse_offers_page
from core.ports.catalog import Storefront

FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures/shopify/products_page.json").read_text()
)
SHOP = "https://example.myshopify.com"


def _variant(sku, variant_id, available=True, price="100.00"):
    return {"id": variant_id, "sku": sku, "available": available, "price": price}


def _page(*variants):
    return {"products": [{"handle": "h", "title": "T", "variants": list(variants)}]}


# --- what the feed means ---------------------------------------------------

def test_the_saved_page_parses_into_offers_keyed_by_sku():
    offers = parse_offers_page(FIXTURE)
    assert set(offers) == {"1729", "1728", "1732"}
    one = offers["1729"]
    assert one.variant_id == 58505164259660
    assert one.available is True
    assert one.price == "680.00"
    assert one.handle == "mds-pick-tecateca-calming-jelly-foam-cleanser"


def test_a_variant_without_a_sku_is_skipped():
    """The sku is the only join to the order history — there is nothing to key
    an offer on without it, and no screen could ever match it to a purchase."""
    assert parse_offers_page(_page(_variant("", 1), _variant("  ", 2))) == {}


def test_a_duplicated_sku_keeps_the_one_that_can_be_bought():
    """Two variants share a sku in this catalogue. Taking the last one blindly
    would hide a buyable product behind its sold-out twin."""
    offers = parse_offers_page(
        _page(_variant("42", 1, available=True), _variant("42", 2, available=False))
    )
    assert offers["42"].variant_id == 1
    assert offers["42"].available is True


def test_a_non_numeric_variant_id_is_skipped_rather_than_guessed():
    """A cart link is addressed to the variant id. One we cannot read is not an
    offer we can offer."""
    assert parse_offers_page(_page({"id": "not-a-number", "sku": "7"})) == {}


# --- what a failed read means ----------------------------------------------

@pytest.fixture()
def transport(monkeypatch):
    """Installs a handler in place of the real socket."""
    def install(handler):
        real = httpx.AsyncClient
        monkeypatch.setattr(
            httpx, "AsyncClient",
            lambda *a, **kw: real(*a, **{**kw, "transport": httpx.MockTransport(handler)}),
        )

    real_sleep = asyncio.sleep
    monkeypatch.setattr(asyncio, "sleep", lambda _s: real_sleep(0))
    return install


def test_every_page_is_read_until_the_feed_runs_out(transport):
    pages = {
        "1": _page(_variant("a", 1)),
        "2": _page(_variant("b", 2)),
        "3": {"products": []},
    }
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        page = dict(request.url.params).get("page", "1")
        seen.append(page)
        return httpx.Response(200, json=pages[page])

    transport(handler)
    offers = asyncio.run(ShopifyStorefront(SHOP).get_offers())
    assert seen == ["1", "2", "3"]
    assert set(offers) == {"a", "b"}


def test_a_failed_page_throws_the_whole_read_away(transport):
    """Half a catalogue is worse than none: the buy button would silently
    disappear from whatever products sat on the pages that never arrived."""
    def handler(request: httpx.Request) -> httpx.Response:
        if dict(request.url.params).get("page") == "1":
            return httpx.Response(200, json=_page(_variant("a", 1)))
        return httpx.Response(500)

    transport(handler)
    assert asyncio.run(ShopifyStorefront(SHOP).get_offers()) == {}


def test_the_adapter_satisfies_the_port():
    assert isinstance(ShopifyStorefront(SHOP), Storefront)
