"""The order refresh actually reaches the APIs.

It did not, between 2026-08-01 and 2026-08-02: commit 2a61ece dropped `phone`
from _refresh_orders and left the inner call passing three arguments to a
function that takes four, so every refresh raised TypeError before touching the
network. Nothing caught it — a spawned refresh is swallowed by bot.tasks, and on
a cold cache the exception left the screen blank, because the bot has no error
handler at all.

These tests are about the wiring, not the formatting: the screens are covered by
nothing, and this is the seam where a customer stops getting fresh orders.
"""
from __future__ import annotations

import asyncio

import pytest

from bot.handlers import orders as handler

CHAT = 555
PHONE = "+380670000000"


class FakeOrders:
    """A client that records the phone it was asked about."""

    def __init__(self, orders: list | None = None) -> None:
        self.asked: list[str] = []
        self._orders = orders or []

    async def get_orders_by_phone(self, phone: str) -> list:
        self.asked.append(phone)
        return list(self._orders)


@pytest.fixture()
def wired(monkeypatch):
    """Everything around the refresh replaced: no database, no network."""
    written: list[tuple[int, list]] = []

    async def fake_phone(chat_id: int) -> str:
        return PHONE

    async def fake_upsert(chat_id: int, rows: list) -> None:
        written.append((chat_id, rows))

    async def fake_save_user(chat_id: int, phone: str, **kw) -> None:
        pass

    monkeypatch.setattr(handler, "get_user_phone", fake_phone)
    monkeypatch.setattr(handler, "upsert_orders", fake_upsert)
    monkeypatch.setattr(handler, "save_user", fake_save_user)
    return written


def test_the_refresh_asks_the_crm(wired):
    """The regression: this raised TypeError and asked nobody anything."""
    keycrm = FakeOrders()
    asyncio.run(handler._refresh_orders(CHAT, keycrm, None))
    assert keycrm.asked == [PHONE], "the refresh never reached KeyCRM"


def test_both_sources_are_asked_when_shopify_is_configured(wired):
    keycrm, shopify = FakeOrders(), FakeOrders()
    asyncio.run(handler._refresh_orders(CHAT, keycrm, shopify))
    assert keycrm.asked == [PHONE]
    assert shopify.asked == [PHONE]


def test_a_user_without_a_phone_is_not_looked_up(monkeypatch, wired):
    """Nobody is registered under an empty number, and asking would return
    somebody else's nothing."""
    async def no_phone(chat_id: int):
        return None

    monkeypatch.setattr(handler, "get_user_phone", no_phone)
    keycrm = FakeOrders()
    asyncio.run(handler._refresh_orders(CHAT, keycrm, None))
    assert keycrm.asked == []


def test_what_the_crm_returns_is_written_to_the_cache(wired):
    from core.adapters.keycrm.parse import KeyCRMOrder

    order = KeyCRMOrder(
        id=900001, status_name="delivered", status_group_id=1,
        grand_total=1450.0, ordered_at="2026-07-14T09:12:33",
    )
    asyncio.run(handler._refresh_orders(CHAT, FakeOrders([order]), None))
    assert len(wired) == 1
    chat_id, rows = wired[0]
    assert chat_id == CHAT
    assert [r["source_order_id"] for r in rows] == ["900001"]


def test_one_broken_source_does_not_stop_the_other(wired):
    """asyncio.gather(return_exceptions=True) is load-bearing: KeyCRM being down
    must not cost the customer their Shopify orders, and vice versa."""
    from core.adapters.shopify.parse import ShopifyOrder

    class Broken:
        async def get_orders_by_phone(self, phone: str) -> list:
            raise RuntimeError("KeyCRM is down")

    shopify_order = ShopifyOrder(
        id="gid://shopify/Order/13025577828684", name="#19966",
        financial_status="PAID", fulfillment_status="FULFILLED",
        total_price="1450.00", currency="UAH", created_at="2026-07-14T09:12:33Z",
    )
    asyncio.run(handler._refresh_orders(CHAT, Broken(), FakeOrders([shopify_order])))
    assert len(wired) == 1
    _chat_id, rows = wired[0]
    assert [r["source"] for r in rows] == ["shopify"]


def test_nothing_is_written_when_there_is_nothing_to_write(wired):
    asyncio.run(handler._refresh_orders(CHAT, FakeOrders(), None))
    assert wired == []
