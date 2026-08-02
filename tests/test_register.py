"""Registration: what it writes, and what it survives.

The flow the whole bot rests on — a verified number bound to a chat — had no
tests, because it lived behind an aiogram Message and an FSMContext. It does
not any more.
"""
from __future__ import annotations

import asyncio

import pytest

from core.domain.order import Order
from core.domain.phone import verified_phone
from core.usecases import register as module
from core.usecases.register import register_customer

CHAT = 555
NUMBER = "+380670000000"

# Built through the real constructor rather than by reaching for the module
# private: the test then exercises the same path the handler does, and a
# VerifiedPhone that only tests can make would prove nothing about it.
PHONE = verified_phone(raw_number=NUMBER, contact_user_id=1, sender_user_id=1)


class FakeKeyCRM:
    def __init__(self, buyer: dict | None = None, orders: list | None = None) -> None:
        self.buyer = buyer
        self.orders = orders or []

    async def get_buyer_by_phone(self, phone: str) -> dict | None:
        return self.buyer

    async def get_orders_by_phone(self, phone: str) -> list:
        return list(self.orders)


class Broken:
    async def get_buyer_by_phone(self, phone: str) -> dict | None:
        raise RuntimeError("the CRM is down")

    async def get_orders_by_phone(self, phone: str) -> list:
        raise RuntimeError("the CRM is down")


@pytest.fixture()
def writes(monkeypatch):
    captured: dict = {"users": [], "orders": []}

    async def fake_save_user(chat_id: int, phone: str, **kw) -> None:
        captured["users"].append((chat_id, phone, kw))

    async def fake_upsert(chat_id: int, rows: list) -> None:
        captured["orders"].append((chat_id, rows))

    monkeypatch.setattr(module, "save_user", fake_save_user)
    monkeypatch.setattr(module, "upsert_orders", fake_upsert)
    return captured


def test_the_number_is_bound_to_the_chat_first(writes):
    """Before anything that can fail: the binding is the point of the flow."""
    asyncio.run(register_customer(CHAT, PHONE, None, None))
    assert writes["users"] == [(CHAT, NUMBER, {})]


def test_a_crm_that_is_down_still_leaves_the_customer_registered(writes):
    """Otherwise the flow would ask for the number again — the one thing it
    must never do, because a typed number cannot prove ownership."""
    asyncio.run(register_customer(CHAT, PHONE, Broken(), Broken()))
    assert writes["users"] == [(CHAT, NUMBER, {})]
    assert writes["orders"] == []


def test_the_profile_is_filled_from_the_crm_when_it_answers(writes):
    keycrm = FakeKeyCRM(buyer={"full_name": "Тесто-Клієнт", "email": "t@example.com"})
    asyncio.run(register_customer(CHAT, PHONE, keycrm, None))
    assert writes["users"][-1] == (
        CHAT, NUMBER, {"full_name": "Тесто-Клієнт", "email": "t@example.com"}
    )


def test_no_buyer_in_the_crm_leaves_the_bare_registration(writes):
    asyncio.run(register_customer(CHAT, PHONE, FakeKeyCRM(buyer=None), None))
    assert writes["users"] == [(CHAT, NUMBER, {})]


def test_the_orders_are_cached_during_registration(writes):
    order = Order(
        source="keycrm", source_order_id="900001",
        status_name="delivered", status_group_id=1,
        grand_total=1450.0, currency="грн", ordered_at="2026-07-14T09:12:33",
    )
    asyncio.run(register_customer(CHAT, PHONE, FakeKeyCRM(orders=[order]), None))
    assert [r["source_order_id"] for _c, rows in writes["orders"] for r in rows] == ["900001"]


def test_registration_without_any_source_configured(writes):
    """Shopify is optional and KeyCRM can be absent in a degraded start."""
    asyncio.run(register_customer(CHAT, PHONE, None, None))
    assert writes["orders"] == []
