"""One physical order seen in both systems must end up as one row.

There is no merge_key in the code yet; today the same job is done by
_DELETE_SHADOWED (bot/db.py) plus the filter in _do_refresh_orders. These tests
pin the observable outcome, so the merge_key rewrite in stage 1 has something to
be checked against rather than being trusted.
"""
from __future__ import annotations

import asyncio

import pytest

from core.repos import orders as orders_repo
from core.repos import base as repos_base
from core.repos.schema import init_db

CHAT = 555
EXTERNAL = "13025577828684"


def _row(source: str, source_order_id: str, *, external_id: str = EXTERNAL,
         status: str = "", name: str = "") -> dict:
    return {
        "chat_id": CHAT,
        "source": source,
        "source_order_id": source_order_id,
        "external_id": external_id,
        "order_name": name,
        "status_name": status,
        "status_group_id": 0,
        "grand_total": 1450.0,
        "currency": "грн",
        "ordered_at": "2026-07-14T09:12:33",
        "products_json": "[]",
        "buyer_name": "",
        "payment_status": "",
        "tracking_code": "",
        "shipping_status": "",
        "delivery_city": "",
        "receive_point": "",
        "recipient_name": "",
    }


KEYCRM = _row("keycrm", "900001", status="delivered", name="#19966")
SHOPIFY = _row("shopify", f"gid://shopify/Order/{EXTERNAL}", status="FULFILLED", name="#19966")


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(repos_base, "DB_PATH", str(tmp_path / "bot_data.db"))
    asyncio.run(init_db())
    return orders_repo


def _cached(db):
    return asyncio.run(db.get_cached_orders(CHAT))


@pytest.mark.parametrize(
    "sequence,ids",
    [([SHOPIFY, KEYCRM], "shopify-then-keycrm"), ([KEYCRM, SHOPIFY], "keycrm-then-shopify")],
    ids=["shopify-first", "keycrm-first"],
)
def test_both_sources_collapse_to_one_row(db, sequence, ids):
    for row in sequence:
        asyncio.run(db.upsert_orders(CHAT, [row]))
    rows = _cached(db)
    assert len(rows) == 1, f"{ids}: {[(r['source'], r['external_id']) for r in rows]}"
    # KeyCRM wins: it is the operational system of record, and it carries the
    # store order number too, so nothing is lost by dropping the Shopify copy.
    assert rows[0]["source"] == "keycrm"


def test_both_in_one_batch_collapse_too(db):
    asyncio.run(db.upsert_orders(CHAT, [SHOPIFY, KEYCRM]))
    rows = _cached(db)
    assert len(rows) == 1
    assert rows[0]["source"] == "keycrm"


def test_three_runs_are_identical(db):
    def run_once():
        asyncio.run(db.upsert_orders(CHAT, [KEYCRM, SHOPIFY]))
        return [
            {k: v for k, v in r.items() if k not in ("id", "synced_at")}
            for r in _cached(db)
        ]

    first, second, third = run_once(), run_once(), run_once()
    assert first == second == third


def test_an_order_with_no_external_id_is_never_collapsed(db):
    """Instagram and Telegram orders have no external id — they must all survive."""
    manual = [_row("keycrm", f"7000{i}", external_id="", name="") for i in range(3)]
    asyncio.run(db.upsert_orders(CHAT, manual))
    assert len(_cached(db)) == 3


def test_two_chats_sharing_a_phone_keep_separate_rows(db):
    """The unique key carries chat_id (core/repos/schema.py, _ORDERS_TABLE_SQL).

    Before it did, the second chat's refresh reassigned the first chat's rows to
    itself and the first customer's history went empty.
    """
    other = dict(KEYCRM, chat_id=CHAT + 1)
    asyncio.run(db.upsert_orders(CHAT, [KEYCRM]))
    asyncio.run(db.upsert_orders(CHAT + 1, [other]))
    assert len(_cached(db)) == 1
    assert len(asyncio.run(db.get_cached_orders(CHAT + 1))) == 1


def test_row_id_survives_a_refresh(db):
    """Was defect #1 in docs/found-during-move.md; fixed by the merge_key work.

    The old upsert was INSERT OR REPLACE, which deletes and reinserts, so the
    AUTOINCREMENT id moved on every refresh. That id is what the expand/collapse
    buttons carry in their callback data, so a button on a screen someone was
    already looking at pointed at a different order. ON CONFLICT DO UPDATE
    updates the row in place, and the id stays.
    """
    asyncio.run(db.upsert_orders(CHAT, [KEYCRM]))
    before = _cached(db)[0]["id"]
    asyncio.run(db.upsert_orders(CHAT, [KEYCRM]))
    assert _cached(db)[0]["id"] == before


# --- the identity rule itself --------------------------------------------

@pytest.mark.parametrize(
    "source,source_order_id,external_id,expected",
    [
        # Both systems' copies of one store order land on one key.
        ("keycrm", "900001", "13025577828684", "shopify:13025577828684"),
        ("shopify", "gid://shopify/Order/13025577828684", "13025577828684",
         "shopify:13025577828684"),
        # No store id: the reporting system's own id, namespaced.
        ("keycrm", "900002", None, "keycrm:900002"),
        ("keycrm", "900002", "", "keycrm:900002"),
        ("demo", "demo-1", None, "demo:demo-1"),
    ],
    ids=["keycrm-side", "shopify-side", "manual-none", "manual-empty", "demo"],
)
def test_merge_key(source, source_order_id, external_id, expected):
    from core.domain.order import merge_key

    assert merge_key(source, source_order_id, external_id) == expected


def test_merge_key_is_always_namespaced():
    """A bare number would collide the day a second channel reports numeric ids,
    and the collision would look like an order overwriting an unrelated one."""
    from core.domain.order import merge_key

    assert ":" in merge_key("keycrm", "1", "1")
    assert not merge_key("keycrm", "1", "1").isdigit()


def test_keycrm_outranks_shopify():
    from core.domain.order import source_rank

    assert source_rank("keycrm") > source_rank("shopify") > source_rank("demo")
    assert source_rank("something-new") == 0
