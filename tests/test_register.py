"""Registration: what it writes, and what it survives.

The flow the whole bot rests on — a verified number bound to a chat — had no
tests, because it lived behind an aiogram Message and an FSMContext. It does
not any more.

**Since commit 19 the writes go through the unit of work**, and the fakes below
are the ports rather than monkeypatched module names. Two of these tests exist
only because that seam moved: the proof of ownership now travels all the way to
storage instead of being unwrapped at the top of the function, and `source` is
carried by `bind_phone` under a write-once rule that both engines have to keep.
"""
from __future__ import annotations

import asyncio

import pytest

from core.domain.order import Order
from core.domain.phone import VerifiedPhone, verified_phone
from core.ports.repositories import CustomerDirectory, UnitOfWork
from core.usecases.register import register_customer

CHAT = 555
NUMBER = "+380670000000"

# Built through the real constructor rather than by reaching for the module
# private: the test then exercises the same path the handler does, and a
# VerifiedPhone that only tests can make would prove nothing about it.
PHONE = verified_phone(raw_number=NUMBER, contact_user_id=1, sender_user_id=1)

# What the surrogate would be under an engine that mints one. Deliberately not
# the chat id: SQLite's are equal, and a test using one number for both could
# not tell the two apart.
USER_ID = 90001


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


class FakeDirectory:
    """core.ports.repositories.CustomerDirectory, recording only what is used."""

    def __init__(self) -> None:
        self.remembered: list[tuple[int, list[str]]] = []
        self.shared: list[int] = []

    async def remember(self, chat_id: int, buyer_ids: set[str]) -> None:
        self.remembered.append((chat_id, sorted(buyer_ids)))

    async def buyers(self) -> list[tuple[int, str]]:
        return []

    async def phones(self) -> list[tuple[int, str]]:
        return []

    async def unidentified(self, *, retry_after_hours: int) -> list[tuple[int, str]]:
        return []

    async def mark_shared(self, chat_id: int) -> None:
        self.shared.append(chat_id)

    async def mark_asked(self, chat_id: int) -> None:
        return None


class FakeProfiles:
    def __init__(self, captured: dict) -> None:
        self._captured = captured

    async def bind_phone(self, chat_id: int, phone: VerifiedPhone, *,
                         source: str = "") -> int:
        self._captured["bound"].append((chat_id, phone, source))
        return USER_ID

    async def update_profile(self, user_id: int, *, full_name=None,
                             email=None) -> None:
        self._captured["profiles"].append(
            (user_id, {"full_name": full_name, "email": email})
        )


class FakeOrders:
    def __init__(self, captured: dict) -> None:
        self._captured = captured

    async def upsert(self, user_id: int, rows: list[dict]) -> None:
        self._captured["orders"].append((user_id, rows))


class FakeUnit:
    def __init__(self, captured: dict) -> None:
        self._captured = captured
        self.orders = FakeOrders(captured)
        self.users = FakeProfiles(captured)

    async def __aenter__(self) -> "FakeUnit":
        self._captured["entered"] += 1
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        self._captured["exited"] += 1

    async def commit(self) -> None:
        self._captured["commits"] += 1


@pytest.fixture()
def writes():
    captured: dict = {"bound": [], "profiles": [], "orders": [], "user_ids": [],
                      "entered": 0, "exited": 0, "commits": 0}

    def factory(*, user_id: int | None = None):
        captured["user_ids"].append(user_id)
        return FakeUnit(captured)

    captured["factory"] = factory
    captured["directory"] = FakeDirectory()
    return captured


def _run(keycrm, writes, *, source: str = "") -> None:
    asyncio.run(register_customer(CHAT, PHONE, keycrm, writes["directory"],
                                  writes["factory"], source=source))


# --- the binding ------------------------------------------------------------

def test_the_number_is_bound_to_the_chat_first(writes):
    """Before anything that can fail: the binding is the point of the flow."""
    _run(None, writes)
    assert writes["bound"] == [(CHAT, PHONE, "")]


def test_the_proof_of_ownership_reaches_storage(writes):
    """It used to be unwrapped to a string at the top of the function. The
    write it guards is the one that decides whose orders a chat can see, so the
    type travels to the last place it still means something — a plain string
    arriving here would compile and cost somebody their history."""
    _run(None, writes)
    [(_chat, bound, _source)] = writes["bound"]
    assert isinstance(bound, VerifiedPhone)
    assert bound.e164 == NUMBER


def test_the_deep_link_travels_with_the_binding(writes):
    """`source` is on bind_phone rather than on a write of its own, because
    this is where a chat becomes a customer and the answer to "who brought
    them" is fixed."""
    _run(None, writes, source="ref_777")
    assert writes["bound"] == [(CHAT, PHONE, "ref_777")]


def test_a_crm_that_is_down_still_leaves_the_customer_registered(writes):
    """Otherwise the flow would ask for the number again — the one thing it
    must never do, because a typed number cannot prove ownership."""
    _run(Broken(), writes)
    assert writes["bound"] == [(CHAT, PHONE, "")]
    assert writes["orders"] == []
    assert writes["commits"] == 1, "the binding is still committed"


# --- what is filled in behind it --------------------------------------------

def test_the_profile_is_filled_from_the_crm_when_it_answers(writes):
    keycrm = FakeKeyCRM(buyer={"full_name": "Тесто-Клієнт", "email": "t@example.com"})
    _run(keycrm, writes)
    assert writes["profiles"] == [
        (USER_ID, {"full_name": "Тесто-Клієнт", "email": "t@example.com"})
    ]


def test_no_buyer_in_the_crm_leaves_the_bare_registration(writes):
    _run(FakeKeyCRM(buyer=None), writes)
    assert writes["profiles"] == []


def test_the_orders_are_cached_during_registration(writes):
    order = Order(
        source="keycrm", source_order_id="900001", buyer_id="6477",
        status_name="delivered", status_group_id=1,
        grand_total=1450.0, currency="грн", ordered_at="2026-07-14T09:12:33",
    )
    _run(FakeKeyCRM(orders=[order]), writes)
    assert [r["source_order_id"] for _c, rows in writes["orders"] for r in rows] == ["900001"]


def test_the_crm_buyer_card_is_recorded_during_registration(writes):
    """The first by-number request a customer ever causes, and the only kind
    that can say which cards they are — the sweep sees orders by card."""
    order = Order(source="keycrm", source_order_id="900001", buyer_id="6477")
    _run(FakeKeyCRM(orders=[order]), writes)
    assert writes["directory"].remembered == [(CHAT, ["6477"])]


def test_registration_without_a_crm_configured(writes):
    """KeyCRM can be absent in a degraded start, and the binding still happens."""
    _run(None, writes)
    assert writes["orders"] == []


# --- what the unit is for ---------------------------------------------------

def test_everything_lands_in_one_unit(writes):
    """The case the UnitOfWork docstring names: binding a number and writing the
    orders behind it must land together, or a crash leaves a customer registered
    with an empty history. The shim underneath still commits per write; what is
    pinned here is that the scenario says which writes belong together."""
    order = Order(source="keycrm", source_order_id="900001", buyer_id="6477")
    _run(FakeKeyCRM(buyer={"full_name": "Т", "email": "t@e.com"}, orders=[order]),
         writes)
    assert (writes["entered"], writes["commits"], writes["exited"]) == (1, 1, 1)


def test_the_unit_is_opened_for_nobody(writes):
    """The one flow where the service context is the honest answer: the person
    does not exist until bind_phone creates them, inside the transaction. This
    is why registration never has to guess that a chat id will do — unlike
    sync_orders, which has nowhere to ask."""
    _run(None, writes)
    assert writes["user_ids"] == [None]


def test_the_writes_after_the_binding_use_the_id_it_returned(writes):
    """Not the chat id. They are the same number under SQLite and are not under
    an engine that mints a surrogate, so a scenario reaching for chat_id here
    would write somebody else's rows the day the engine changes."""
    order = Order(source="keycrm", source_order_id="900001", buyer_id="6477")
    _run(FakeKeyCRM(buyer={"full_name": "Т", "email": "t@e.com"}, orders=[order]),
         writes)
    assert [user_id for user_id, _ in writes["profiles"]] == [USER_ID]
    assert [user_id for user_id, _ in writes["orders"]] == [USER_ID]


def test_the_fakes_satisfy_the_ports_they_stand_for(writes):
    """Or they are testing a fiction."""
    assert isinstance(writes["directory"], CustomerDirectory)
    assert isinstance(writes["factory"](), UnitOfWork)
