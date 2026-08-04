"""The order-sync scenario, with no Telegram anywhere near it.

This is what moving it out of the handler bought: the cases below need no
message, no keyboard and no chat — only one object that answers
get_orders_by_phone. Where it used to live, the same coverage would have meant
building an aiogram Message first, which is why it had none and why §13 shipped.

Shopify was the second source here until stage 4. The tests that pinned the
two-source behaviour went with it — §4.4 says the branch and its test come back
together if a webhook ever brings the second writer back.
"""
from __future__ import annotations

import asyncio

import pytest

from core.domain.order import Order
from core.usecases import sync_orders as module
from core.usecases.sync_orders import sync_orders

CHAT = 555
PHONE = "+380670000000"
BUYER = "6477"


class FakeSource:
    """Answers with a fixed list and remembers what it was asked."""

    def __init__(self, orders: list | None = None) -> None:
        self.asked: list[str] = []
        self._orders = orders or []

    async def get_orders_by_phone(self, phone: str) -> list:
        self.asked.append(phone)
        return list(self._orders)


def _order(order_id: int = 900001, **kw) -> Order:
    """An order as the KeyCRM adapter would have parsed it — the scenario never
    learns which adapter that was."""
    return Order(
        source="keycrm", source_order_id=str(order_id), buyer_id=BUYER,
        status_name="delivered", status_group_id=1,
        grand_total=1450.0, currency="грн", ordered_at="2026-07-14T09:12:33", **kw,
    )


@pytest.fixture()
def writes(monkeypatch):
    """Captures what the scenario would have written, without a database."""
    captured: dict = {"orders": [], "profiles": [], "buyers": []}

    async def fake_upsert(chat_id: int, rows: list) -> None:
        captured["orders"].append((chat_id, rows))

    async def fake_save_user(chat_id: int, phone: str, **kw) -> None:
        captured["profiles"].append((chat_id, kw))

    async def fake_remember(chat_id: int, buyer_ids: set) -> None:
        captured["buyers"].append((chat_id, sorted(buyer_ids)))

    monkeypatch.setattr(module, "upsert_orders", fake_upsert)
    monkeypatch.setattr(module, "save_user", fake_save_user)
    monkeypatch.setattr(module, "remember_crm_buyers", fake_remember)
    return captured


def test_the_crm_is_asked_for_this_number(writes):
    keycrm = FakeSource()
    asyncio.run(sync_orders(CHAT, PHONE, keycrm))
    assert keycrm.asked == [PHONE]


def test_what_came_back_is_written(writes):
    asyncio.run(sync_orders(CHAT, PHONE, FakeSource([_order()])))
    assert [r["source_order_id"] for _c, rows in writes["orders"] for r in rows] == ["900001"]


def test_nothing_found_writes_nothing(writes):
    """An empty answer is not an error, and an empty upsert is not a write."""
    asyncio.run(sync_orders(CHAT, PHONE, FakeSource()))
    assert writes["orders"] == []
    assert writes["buyers"] == []


def test_the_crm_buyer_card_is_recorded(writes):
    """The reason this path matters to the sweep, which never sees a number:
    only a by-number request can say which cards a chat is."""
    asyncio.run(sync_orders(CHAT, PHONE, FakeSource([_order(), _order(900002)])))
    assert writes["buyers"] == [(CHAT, [BUYER])]


def test_the_buyer_profile_is_refreshed_from_the_first_order(writes):
    keycrm = FakeSource([_order(buyer_name="Тесто-Клієнт", buyer_email="t@example.com")])
    asyncio.run(sync_orders(CHAT, PHONE, keycrm))
    assert writes["profiles"] == [
        (CHAT, {"full_name": "Тесто-Клієнт", "email": "t@example.com"})
    ]


def test_an_order_without_buyer_details_leaves_the_profile_alone(writes):
    """Blank values would overwrite a name the customer already has."""
    asyncio.run(sync_orders(CHAT, PHONE, FakeSource([_order()])))
    assert writes["profiles"] == []


def test_a_failing_profile_write_does_not_lose_the_orders(writes, monkeypatch):
    """The profile refresh is a nicety; the orders are the reason for the call."""
    async def boom(*a, **kw):
        raise RuntimeError("users table is locked")

    monkeypatch.setattr(module, "save_user", boom)
    keycrm = FakeSource([_order(buyer_name="Тесто-Клієнт")])
    asyncio.run(sync_orders(CHAT, PHONE, keycrm))
    assert [r["source_order_id"] for _c, rows in writes["orders"] for r in rows] == ["900001"]
