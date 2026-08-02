"""Snapshot tests: the parsers keep producing exactly what they produce today.

These pin behaviour, not correctness. Where the current output is wrong it is
still what the snapshot says, and the case carries a comment pointing at
docs/found-during-move.md. Fixing comes after the move, so that a change in
behaviour during it is always visible as a failing test rather than as a
customer noticing.
"""
from __future__ import annotations

import json
import pathlib
from dataclasses import asdict

import pytest

from core.adapters.keycrm.parse import (_parse_order, keycrm_order_to_dict,
                                        last_page, parse_buyer, parse_orders,
                                        parse_stock_page)
from core.adapters.shopify.parse import (_parse_shopify_order,
                                         parse_orders as parse_shopify_orders,
                                         shopify_order_to_dict)
from core.domain.order import shopify_external_id

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
SNAPSHOTS = pathlib.Path(__file__).parent / "snapshots"

KEYCRM_ORDERS = sorted((FIXTURES / "keycrm").glob("order_*.json"))


def _snapshot(name: str) -> dict:
    return json.loads((SNAPSHOTS / name).read_text())


@pytest.mark.parametrize("path", KEYCRM_ORDERS, ids=lambda p: p.stem)
def test_keycrm_parser_output_is_unchanged(path):
    expected = _snapshot("keycrm_parser.json")[path.stem]
    order = _parse_order(json.loads(path.read_text()))
    assert asdict(order) == expected["parsed"]
    assert keycrm_order_to_dict(order, chat_id=777) == expected["as_row"]


def test_every_keycrm_edge_case_has_a_fixture():
    """The set of cases is itself the contract; adding one must be deliberate."""
    assert {p.stem for p in KEYCRM_ORDERS} == {
        "order_delivered_with_ttn",
        "order_new_without_ttn",
        "order_cancelled",
        "order_multi_item",
        "order_from_shopify",
        "order_without_items",
    }


def test_keycrm_fixtures_keep_the_fields_the_parser_ignores():
    """A fixture trimmed to what the parser reads would not catch a schema change.

    The live response carries 50 keys per order; the parser reads 12 of them.
    """
    raw = json.loads((FIXTURES / "keycrm" / "order_delivered_with_ttn.json").read_text())
    assert len(raw) >= 45
    for ignored in ("manager_comment", "margin_sum", "promocode", "warehouse",
                    "updated_at", "status_changed_at", "payments_total"):
        assert ignored in raw


def test_keycrm_list_envelope_carries_pagination_the_client_ignores():
    """get_orders_by_phone sends no `page` and reads no `last_page`.

    Orders past the first 50 are dropped silently. Pinned here so the day the
    client starts paginating, this test is what changes.
    """
    env = json.loads((FIXTURES / "keycrm" / "list_envelope.json").read_text())
    assert {"last_page", "total", "next_page_url", "per_page"} <= set(env)


def _envelope() -> dict:
    return json.loads((FIXTURES / "keycrm" / "list_envelope.json").read_text())


def test_keycrm_envelope_unpacks_into_the_orders_it_carries():
    """Reachable without a network for the first time.

    Until the client was split, this line lived inside `async with
    httpx.AsyncClient` and could only be exercised through a mock transport.
    """
    env = _envelope()
    assert [o.id for o in parse_orders(env)] == [raw["id"] for raw in env["data"]]


def test_an_envelope_without_data_parses_to_nothing():
    """The shape of a phone with no orders — the common case, not an error."""
    assert parse_orders({}) == []
    assert parse_orders({"data": []}) == []


def test_the_buyer_comes_from_the_first_order():
    buyer = _envelope()["data"][0]["buyer"]
    assert parse_buyer(_envelope()) == {
        "full_name": buyer["full_name"],
        "email": buyer["email"],
    }


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"data": []},
        {"data": [{"buyer": None}]},
        {"data": [{"buyer": {"full_name": "", "email": ""}}]},
    ],
    ids=["no-envelope", "no-orders", "no-buyer", "empty-buyer"],
)
def test_a_buyer_with_nothing_in_it_is_no_buyer(body):
    """None, not an empty profile: the caller writes whatever it gets to the
    user record, and a blank name would overwrite a real one."""
    assert parse_buyer(body) is None


def test_stock_page_is_quantity_minus_reserve():
    """Constructed, not recorded — /offers/stocks needs a live key, and the
    field names here are the ones the client reads (docs/found-during-move.md
    §6 makes the same distinction for the Shopify fixtures)."""
    body = {"data": [
        {"sku": "KS-001", "quantity": 10, "reserve": 3},
        {"sku": "KS-002", "quantity": 5, "reserve": 0},
        {"sku": "KS-003", "quantity": 2, "reserve": 4},
    ]}
    assert parse_stock_page(body) == {"KS-001": 7, "KS-002": 5, "KS-003": -2}


@pytest.mark.parametrize(
    "offer",
    [{"quantity": 1}, {"sku": None}, {"sku": ""}, {"sku": "   "}],
    ids=["missing", "null", "empty", "whitespace"],
)
def test_an_offer_without_a_sku_is_skipped(offer):
    """The sku is the only name the rest of the system knows a product by, so an
    offer without one cannot be matched to a subscription."""
    assert parse_stock_page({"data": [offer]}) == {}


def test_last_page_defaults_to_one_when_the_field_is_missing():
    """A missing last_page must stop the sweep, not loop it: get_stock breaks on
    `page >= last_page(body)`."""
    assert last_page(_envelope()) == 1814
    assert last_page({}) == 1
    assert last_page({"last_page": None}) == 1


@pytest.mark.parametrize("order_name", ["#19966", "#19801"])
def test_shopify_parser_output_is_unchanged(order_name):
    expected = _snapshot("shopify_parser.json")[order_name]
    raw = json.loads((FIXTURES / "shopify" / "customer_with_orders.json").read_text())
    nodes = {e["node"]["name"]: e["node"]
             for e in raw["data"]["customers"]["edges"][0]["node"]["orders"]["edges"]}
    order = _parse_shopify_order(nodes[order_name])
    assert asdict(order) == expected["parsed"]
    assert shopify_order_to_dict(order, chat_id=777) == expected["as_row"]
    assert shopify_external_id(order.id) == expected["external_id"]


@pytest.mark.parametrize(
    "gid,expected",
    [
        ("gid://shopify/Order/13025577828684", "13025577828684"),
        ("gid://shopify/Order/", ""),
        ("gid://shopify/Order/not-a-number", ""),
        ("", ""),
    ],
)
def test_shopify_external_id_edge_cases(gid, expected):
    assert shopify_external_id(gid) == expected


def _shopify(fixture: str) -> dict:
    return json.loads((FIXTURES / "shopify" / fixture).read_text())


def test_the_shopify_envelope_unpacks_into_the_customers_orders():
    """Newest first, as the query asks (sortKey: CREATED_AT, reverse: true)."""
    orders = parse_shopify_orders(_shopify("customer_with_orders.json"))
    assert [o.name for o in orders] == ["#19966", "#19801"]


def test_a_phone_matching_no_customer_parses_to_nothing():
    """The empty-edges guard: without it the first-customer lookup would raise
    IndexError on every customer the store has never seen."""
    assert parse_shopify_orders(_shopify("customer_not_found.json")) == []


def test_a_graphql_error_envelope_carries_no_orders():
    """The client returns [] here too, but only because it checks `errors`
    first and logs them. This pins the half that does not depend on logging."""
    assert parse_shopify_orders(_shopify("graphql_errors.json")) == []


def test_shopify_fixtures_are_marked_as_reconstructed():
    """No Shopify credentials are configured, so none of these are recordings.

    Left explicit in the files: a reconstructed fixture cannot catch a field
    Shopify sends that we never thought to include.
    """
    for path in (FIXTURES / "shopify").glob("*.json"):
        assert "RECONSTRUCTED" in json.loads(path.read_text())["_fixture_note"]


def test_keycrm_reports_one_shipment_per_order():
    """The "multiple shipments" edge case the brief asked for does not exist.

    KeyCRM returns `shipping` as a single object, not a list: measured across
    150 live orders, every one of them a dict, and all 121 orders carrying a
    tracking code carry exactly one string. So an order with two parcels is not
    representable in the response this client reads, and no fixture can hold
    one. order_multi_item covers multiple *line items*, which is a different
    thing and was substituted for it.

    Pinned rather than left as a note: if KeyCRM ever turns `shipping` into a
    list, every consumer of tracking_code starts reading the wrong shape, and
    this is the test that says so.
    """
    for path in KEYCRM_ORDERS:
        shipping = json.loads(path.read_text()).get("shipping")
        assert shipping is None or isinstance(shipping, dict), (
            f"{path.name}: shipping became a {type(shipping).__name__}"
        )
        if shipping:
            # None on an order that has not shipped, a string once it has —
            # never a list. The KeyCRM parser absorbs the None with `or ""`.
            tracking = shipping.get("tracking_code")
            assert tracking is None or isinstance(tracking, str)
