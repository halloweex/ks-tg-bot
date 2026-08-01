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

from bot.services.keycrm import _parse_order, keycrm_order_to_dict
from bot.services.shopify import (_parse_shopify_order, shopify_external_id,
                                  shopify_order_to_dict)

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
            # never a list. bot/services/keycrm.py absorbs the None with `or ""`.
            tracking = shipping.get("tracking_code")
            assert tracking is None or isinstance(tracking, str)
