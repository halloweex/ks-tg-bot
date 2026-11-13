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
from core.domain.promo import Promo
from core.ports.discounts import (DiscountCodes, DiscountLookup,
                                   DiscountLookupFailed)

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


# --- reading a code marketing created --------------------------------------------

# The answer the live store gave on 2026-11-13 for one of its codes, renamed.
FOUND = {"data": {"codeDiscountNodeByCode": {
    "id": "gid://shopify/DiscountCodeNode/2294357131596",
    "codeDiscount": {
        "__typename": "DiscountCodeBasic", "title": "NOV20 (листопад)",
        "status": "ACTIVE", "startsAt": "2026-11-13T03:52:24Z",
        "endsAt": "2026-11-15T03:52:24Z", "usageLimit": 1, "asyncUsageCount": 0,
        "customerGets": {"value": {"__typename": "DiscountPercentage",
                                   "percentage": 0.1}}}}}}


def _find(code: str = "NOV20"):
    return asyncio.run(ShopifyDiscounts(STORE, "shpat_test").find(code))


def test_the_adapter_also_satisfies_the_lookup_port():
    assert isinstance(ShopifyDiscounts(STORE, "t"), DiscountLookup)


def test_a_code_the_shop_has_comes_back_with_its_terms(transport):
    transport["install"](lambda request: httpx.Response(200, json=FOUND))

    found = _find()

    assert found == Promo(
        code="NOV20", title="NOV20 (листопад)", status="ACTIVE",
        starts_at=found.starts_at, ends_at=found.ends_at,
        usage_limit=1, used=0, percent=10.0, amount=None)
    assert found.ends_at.isoformat() == "2026-11-15T03:52:24+00:00"
    assert transport["sent"]()["variables"] == {"code": "NOV20"}


def test_a_fixed_amount_is_read_as_money(transport):
    body = {"data": {"codeDiscountNodeByCode": {"codeDiscount": {
        "__typename": "DiscountCodeBasic", "title": "200 off", "status": "ACTIVE",
        "startsAt": None, "endsAt": None, "usageLimit": None, "asyncUsageCount": 3,
        "customerGets": {"value": {"__typename": "DiscountAmount",
                                   "amount": {"amount": "200.0", "currencyCode": "UAH"}}}}}}}
    transport["install"](lambda request: httpx.Response(200, json=body))

    found = _find()

    assert (found.amount, found.percent, found.ends_at) == ("200 UAH", None, None)


def test_a_code_the_shop_does_not_have_is_none(transport):
    """What the live store answered for a code that does not exist: a null
    node, not an error."""
    transport["install"](lambda request: httpx.Response(
        200, json={"data": {"codeDiscountNodeByCode": None}}))
    assert _find("NOV2O") is None


@pytest.mark.parametrize("answer", [
    lambda request: httpx.Response(500),
    lambda request: httpx.Response(200, json={"errors": [{"message": "Access denied"}]}),
    lambda request: httpx.Response(200, text="<html>maintenance</html>"),
])
def test_a_shop_that_cannot_be_asked_is_never_read_as_no_such_code(transport, answer):
    transport["install"](answer)
    with pytest.raises(DiscountLookupFailed):
        _find()
