"""Creating a promo code, and every way that can go wrong quietly.

The only write this system makes to the shop. What it must never do is hand
back a code that does not exist: the reward message is built around the code,
and a customer who is given a dead one finds out at the checkout.
"""
from __future__ import annotations

import asyncio

import httpx
import pytest

from core.adapters.shopify.discounts import ShopifyDiscounts
from core.ports.discounts import DiscountCodes

STORE = "qy2jmd-ui.myshopify.com"


@pytest.fixture()
def transport(monkeypatch):
    box: dict = {"requests": []}

    def install(handler):
        def wrapped(request: httpx.Request) -> httpx.Response:
            box["requests"].append(request)
            return handler(request)

        real = httpx.AsyncClient
        monkeypatch.setattr(
            httpx, "AsyncClient",
            lambda *a, **kw: real(*a, **{**kw, "transport": httpx.MockTransport(wrapped)}),
        )

    box["install"] = install
    box["sent"] = lambda: __import__("json").loads(box["requests"][0].content)
    return box


def _issue(percent: int = 10) -> str | None:
    client = ShopifyDiscounts(STORE, "shpat_test")
    return asyncio.run(client.issue_percentage(percent, title="Referral, Оля"))


def _ok(_request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"data": {"discountCodeBasicCreate": {
        "codeDiscountNode": {"id": "gid://shopify/DiscountCodeNode/1"},
        "userErrors": [],
    }}})


def test_the_adapter_satisfies_the_port():
    assert isinstance(ShopifyDiscounts(STORE, "t"), DiscountCodes)


def test_a_created_code_comes_back(transport):
    transport["install"](_ok)
    code = _issue()
    assert code and code.startswith("KS-")
    assert len(code) == len("KS-") + 8


def test_the_code_is_the_one_that_was_asked_for(transport):
    """The code Shopify stores has to be the code the customer is told, and the
    only way to be sure is to send it rather than read it back from a summary
    nobody parses."""
    transport["install"](_ok)
    code = _issue()
    assert transport["sent"]()["variables"]["discount"]["code"] == code


def test_ten_per_cent_is_sent_as_a_fraction(transport):
    """Shopify reads 0.1 as ten per cent and 10 as a thousand."""
    transport["install"](_ok)
    _issue(10)
    gets = transport["sent"]()["variables"]["discount"]["customerGets"]
    assert gets["value"]["percentage"] == 0.1
    assert gets["items"] == {"all": True}


def test_a_reward_cannot_be_spent_twice(transport):
    transport["install"](_ok)
    _issue()
    discount = transport["sent"]()["variables"]["discount"]
    assert discount["usageLimit"] == 1
    assert discount["appliesOncePerCustomer"] is True


def test_no_customer_selection_is_sent(transport):
    """This API version has no such field on the input, and sending one fails
    the whole call. Read off the store's own schema, not the documentation."""
    transport["install"](_ok)
    _issue()
    assert "customerSelection" not in transport["sent"]()["variables"]["discount"]


# --- and every way it fails -------------------------------------------------

def test_a_rejected_input_is_not_a_code(transport):
    transport["install"](lambda _r: httpx.Response(200, json={
        "data": {"discountCodeBasicCreate": {
            "codeDiscountNode": None,
            "userErrors": [{"field": ["code"], "message": "Code must be unique"}],
        }}}))
    assert _issue() is None


def test_graphql_errors_are_not_a_code(transport):
    transport["install"](lambda _r: httpx.Response(200, json={
        "errors": [{"message": "Access denied for discountCodeBasicCreate"}]}))
    assert _issue() is None


def test_a_dead_shop_is_not_a_code(transport):
    transport["install"](lambda _r: httpx.Response(503, text="down"))
    assert _issue() is None


def test_a_nonsense_body_is_not_a_code(transport):
    transport["install"](lambda _r: httpx.Response(200, text="<html>maintenance"))
    assert _issue() is None


def test_zero_per_cent_asks_for_nothing(transport):
    """A reward of nothing is a configuration mistake, and the shop should not
    be asked to record one."""
    transport["install"](_ok)
    assert _issue(0) is None
    assert not transport["requests"]
