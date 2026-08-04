"""One physical order ends up as one row, however many times it is read.

Written before merge_key existed, when the same job was done by a sweep that
deleted the Shopify copy after every refresh. The tests pin the observable
outcome rather than the mechanism, which is why they survived that rewrite, the
move of the queries into core/repos/orders.py, and now the departure of the
second writer — the cases about which source outranked which went with it (§4.4)
and come back with it.
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
# The Shopify-shaped row that used to sit here went with the write path (§4.4).
# What it proved — that both systems' copies land on one merge_key — is still
# proved by test_merge_key below, which is where the rule lives.


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(repos_base, "DB_PATH", str(tmp_path / "bot_data.db"))
    asyncio.run(init_db())
    return orders_repo


def _cached(db):
    return asyncio.run(db.get_cached_orders(CHAT))


def test_the_same_order_read_twice_stays_one_row(db):
    """Which happens on every sweep: the window and the orders screen both write
    the same order, and the unique key is what keeps that one row."""
    asyncio.run(db.upsert_orders(CHAT, [KEYCRM]))
    asyncio.run(db.upsert_orders(CHAT, [dict(KEYCRM, status_name="completed")]))
    rows = _cached(db)
    assert len(rows) == 1
    assert rows[0]["status_name"] == "completed"


def test_the_later_read_wins(db):
    """§4.4, since Shopify left the write path. There used to be a CASE WHEN per
    column comparing source_rank, so a junior source could not overwrite a
    senior one; with one writer it was always true, and unexercised conditional
    code is what that section asks to delete rather than keep."""
    asyncio.run(db.upsert_orders(CHAT, [dict(KEYCRM, status_name="delivered")]))
    asyncio.run(db.upsert_orders(CHAT, [dict(KEYCRM, status_name="returned")]))
    assert _cached(db)[0]["status_name"] == "returned"


def test_a_field_the_newer_read_does_not_carry_is_not_erased(db):
    """_KEEP_BEST, and it fires on real data every sweep: an Instagram order has
    no external id and no store order number."""
    asyncio.run(db.upsert_orders(CHAT, [KEYCRM]))
    asyncio.run(db.upsert_orders(CHAT, [dict(KEYCRM, order_name="")]))
    assert _cached(db)[0]["order_name"] == "#19966"


def test_three_runs_are_identical(db):
    def run_once():
        asyncio.run(db.upsert_orders(CHAT, [KEYCRM]))
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


def test_the_source_ranking_is_still_written_down():
    """Nothing branches on it since §4.4 — the column is a mark of provenance
    now. The order is kept because the branch returns with a second writer, and
    reconstructing which source outranks which afterwards is guesswork."""
    from core.domain.order import source_rank

    assert source_rank("keycrm") > source_rank("shopify") > source_rank("demo")
    assert source_rank("something-new") == 0
