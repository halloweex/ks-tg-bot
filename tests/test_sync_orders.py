"""The order-sync scenario, with no Telegram anywhere near it.

This is what moving it out of the handler bought: the cases below need no
message, no keyboard and no chat — only two objects that answer
get_orders_by_phone. Where it used to live, the same coverage would have meant
building an aiogram Message first, which is why it had none and why §13 shipped.
"""
from __future__ import annotations

import asyncio

import pytest

from core.domain.order import Order
from core.usecases import sync_orders as module
from core.usecases.sync_orders import sync_orders

CHAT = 555
PHONE = "+380670000000"


class FakeSource:
    """Answers with a fixed list and remembers what it was asked."""

    def __init__(self, orders: list | None = None) -> None:
        self.asked: list[str] = []
        self._orders = orders or []

    async def get_orders_by_phone(self, phone: str) -> list:
        self.asked.append(phone)
        return list(self._orders)


class BrokenSource:
    async def get_orders_by_phone(self, phone: str) -> list:
        raise RuntimeError("the CRM is down")


def _keycrm_order(order_id: int = 900001, **kw) -> Order:
    """An order as the KeyCRM adapter would have parsed it — the scenario never
    learns which adapter that was."""
    return Order(
        source="keycrm", source_order_id=str(order_id),
        status_name="delivered", status_group_id=1,
        grand_total=1450.0, currency="грн", ordered_at="2026-07-14T09:12:33", **kw,
    )


def _shopify_order() -> Order:
    return Order(
        source="shopify", source_order_id="gid://shopify/Order/13025577828684",
        external_id="13025577828684", order_name="#19966",
        status_name="FULFILLED", grand_total=1450.0, currency="UAH",
        ordered_at="2026-07-14T09:12:33Z",
        payment_status="PAID", shipping_status="FULFILLED",
    )


@pytest.fixture()
def writes(monkeypatch):
    """Captures what the scenario would have written, without a database."""
    captured: dict = {"orders": [], "profiles": []}

    async def fake_upsert(chat_id: int, rows: list) -> None:
        captured["orders"].append((chat_id, rows))

    async def fake_save_user(chat_id: int, phone: str, **kw) -> None:
        captured["profiles"].append((chat_id, kw))

    monkeypatch.setattr(module, "upsert_orders", fake_upsert)
    monkeypatch.setattr(module, "save_user", fake_save_user)
    return captured


def test_both_sources_are_asked(writes):
    keycrm, shopify = FakeSource(), FakeSource()
    asyncio.run(sync_orders(CHAT, PHONE, keycrm, shopify))
    assert keycrm.asked == [PHONE]
    assert shopify.asked == [PHONE]


def test_shopify_is_optional(writes):
    keycrm = FakeSource([_keycrm_order()])
    asyncio.run(sync_orders(CHAT, PHONE, keycrm, None))
    assert [r["source_order_id"] for _c, rows in writes["orders"] for r in rows] == ["900001"]


def test_a_broken_source_does_not_cost_the_other_one(writes):
    """gather(return_exceptions=True) is the whole point: KeyCRM being down must
    not take the customer's Shopify orders with it."""
    asyncio.run(sync_orders(CHAT, PHONE, BrokenSource(), FakeSource([_shopify_order()])))
    assert [r["source"] for _c, rows in writes["orders"] for r in rows] == ["shopify"]


def test_both_sources_broken_writes_nothing(writes):
    asyncio.run(sync_orders(CHAT, PHONE, BrokenSource(), BrokenSource()))
    assert writes["orders"] == []


def test_nothing_found_writes_nothing(writes):
    """An empty answer is not an error, and an empty upsert is not a write."""
    asyncio.run(sync_orders(CHAT, PHONE, FakeSource(), FakeSource()))
    assert writes["orders"] == []


def test_the_buyer_profile_is_refreshed_from_the_first_order(writes):
    keycrm = FakeSource([_keycrm_order(buyer_name="Тесто-Клієнт", buyer_email="t@example.com")])
    asyncio.run(sync_orders(CHAT, PHONE, keycrm, None))
    assert writes["profiles"] == [
        (CHAT, {"full_name": "Тесто-Клієнт", "email": "t@example.com"})
    ]


def test_an_order_without_buyer_details_leaves_the_profile_alone(writes):
    """Blank values would overwrite a name the customer already has."""
    asyncio.run(sync_orders(CHAT, PHONE, FakeSource([_keycrm_order()]), None))
    assert writes["profiles"] == []


def test_a_failing_profile_write_does_not_lose_the_orders(writes, monkeypatch):
    """The profile refresh is a nicety; the orders are the reason for the call."""
    async def boom(*a, **kw):
        raise RuntimeError("users table is locked")

    monkeypatch.setattr(module, "save_user", boom)
    keycrm = FakeSource([_keycrm_order(buyer_name="Тесто-Клієнт")])
    asyncio.run(sync_orders(CHAT, PHONE, keycrm, None))
    assert [r["source_order_id"] for _c, rows in writes["orders"] for r in rows] == ["900001"]
