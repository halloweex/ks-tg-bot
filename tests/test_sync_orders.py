"""The order-sync scenario, with no Telegram anywhere near it.

This is what moving it out of the handler bought: the cases below need no
message, no keyboard and no chat — only one object that answers
get_orders_by_phone. Where it used to live, the same coverage would have meant
building an aiogram Message first, which is why it had none and why §13 shipped.

Shopify was the second source here until stage 4. The tests that pinned the
two-source behaviour went with it — §4.4 says the branch and its test come back
together if a webhook ever brings the second writer back.

**Since commit 18 the writes go through ports rather than through module
attributes**, and the fakes below are the ports rather than monkeypatched names.
That is not a tidier spelling of the same thing: patching `module.save_user`
could not notice the scenario calling it with the wrong number of arguments,
which is the §13 failure, while a fake that has to satisfy the protocol can. One
of these tests exists only because the seam moved — the profile write no longer
carries a phone, and something has to say that the same number still lands.
"""
from __future__ import annotations

import asyncio

import pytest

from core.domain.order import Order
from core.ports.repositories import CustomerDirectory
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


class FakeDirectory:
    """core.ports.repositories.CustomerDirectory, remembering only the writes.

    Four of the five methods are here because the protocol has five; this
    scenario calls one. They answer emptily rather than raising, because a fake
    that blows up on an unused method is a fake that fails the day somebody adds
    a call and forgets to look here.
    """

    def __init__(self) -> None:
        self.remembered: list[tuple[int, list[str]]] = []
        self.asked: list[int] = []

    async def remember(self, chat_id: int, buyer_ids: set[str]) -> None:
        self.remembered.append((chat_id, sorted(buyer_ids)))

    async def buyers(self) -> list[tuple[int, str]]:
        return []

    async def phones(self) -> list[tuple[int, str]]:
        return []

    async def unidentified(self, *, retry_after_hours: int) -> list[tuple[int, str]]:
        return []

    async def mark_asked(self, chat_id: int) -> None:
        self.asked.append(chat_id)


class FakeOrders:
    def __init__(self, writes: list) -> None:
        self._writes = writes

    async def upsert(self, user_id: int, rows: list[dict]) -> None:
        self._writes.append((user_id, rows))


class FakeProfiles:
    def __init__(self, writes: list, raises: Exception | None = None) -> None:
        self._writes = writes
        self._raises = raises

    async def bind_phone(self, chat_id: int, phone) -> int:
        raise AssertionError("this scenario must never bind a number")

    async def update_profile(self, user_id: int, *, full_name=None, email=None) -> None:
        if self._raises is not None:
            raise self._raises
        self._writes.append((user_id, {"full_name": full_name, "email": email}))


class FakeUnit:
    """One unit, and a record of whether the scenario closed it properly."""

    def __init__(self, captured: dict, profile_error: Exception | None = None) -> None:
        self.captured = captured
        self.orders = FakeOrders(captured["orders"])
        self.users = FakeProfiles(captured["profiles"], profile_error)
        self.user_id: int | None = None

    async def __aenter__(self) -> "FakeUnit":
        self.captured["entered"] += 1
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        self.captured["exited"] += 1

    async def commit(self) -> None:
        self.captured["commits"] += 1


@pytest.fixture()
def writes():
    """What the scenario would have written, and how it framed the writing."""
    captured: dict = {"orders": [], "profiles": [], "entered": 0, "exited": 0,
                      "commits": 0, "user_ids": []}

    def factory(*, user_id: int | None = None):
        captured["user_ids"].append(user_id)
        unit = FakeUnit(captured, captured.get("profile_error"))
        unit.user_id = user_id
        return unit

    captured["factory"] = factory
    captured["directory"] = FakeDirectory()
    return captured


def _run(keycrm, writes) -> None:
    asyncio.run(sync_orders(CHAT, PHONE, keycrm, writes["directory"],
                            writes["factory"]))


def _order(order_id: int = 900001, **kw) -> Order:
    """An order as the KeyCRM adapter would have parsed it — the scenario never
    learns which adapter that was."""
    return Order(
        source="keycrm", source_order_id=str(order_id), buyer_id=BUYER,
        status_name="delivered", status_group_id=1,
        grand_total=1450.0, currency="грн", ordered_at="2026-07-14T09:12:33", **kw,
    )


# --- what it asks and what it writes ----------------------------------------

def test_the_crm_is_asked_for_this_number(writes):
    keycrm = FakeSource()
    _run(keycrm, writes)
    assert keycrm.asked == [PHONE]


def test_what_came_back_is_written(writes):
    _run(FakeSource([_order()]), writes)
    assert [r["source_order_id"] for _c, rows in writes["orders"] for r in rows] == ["900001"]


def test_nothing_found_writes_nothing(writes):
    """An empty answer is not an error, and an empty upsert is not a write."""
    _run(FakeSource(), writes)
    assert writes["orders"] == []
    assert writes["directory"].remembered == []
    assert writes["entered"] == 0, "no unit is opened for nothing to write"


def test_the_crm_buyer_card_is_recorded(writes):
    """The reason this path matters to the sweep, which never sees a number:
    only a by-number request can say which cards a chat is."""
    _run(FakeSource([_order(), _order(900002)]), writes)
    assert writes["directory"].remembered == [(CHAT, [BUYER])]


def test_the_buyer_profile_is_refreshed_from_the_first_order(writes):
    keycrm = FakeSource([_order(buyer_name="Тесто-Клієнт", buyer_email="t@example.com")])
    _run(keycrm, writes)
    assert writes["profiles"] == [
        (CHAT, {"full_name": "Тесто-Клієнт", "email": "t@example.com"})
    ]


def test_an_order_without_buyer_details_leaves_the_profile_alone(writes):
    """Blank values would overwrite a name the customer already has."""
    _run(FakeSource([_order()]), writes)
    assert writes["profiles"] == []


def test_a_failing_profile_write_does_not_lose_the_orders(writes):
    """The profile refresh is a nicety; the orders are the reason for the call."""
    writes["profile_error"] = RuntimeError("users table is locked")
    _run(FakeSource([_order(buyer_name="Тесто-Клієнт")]), writes)
    assert [r["source_order_id"] for _c, rows in writes["orders"] for r in rows] == ["900001"]


# --- what the unit is for ---------------------------------------------------

def test_the_two_writes_share_one_unit_and_it_is_committed(writes):
    """The first scenario in the tree to open one. The shim underneath still
    commits per write — what this pins is that the scenario says which writes
    belong together, so the day a real transaction goes under it, they do."""
    _run(FakeSource([_order(buyer_name="Тесто-Клієнт")]), writes)
    assert (writes["entered"], writes["commits"], writes["exited"]) == (1, 1, 1)


def test_the_unit_is_opened_for_this_customer_and_not_for_the_service(writes):
    """user_id=None is the worker acting for nobody, and this is not that. It is
    the chat id because under SQLite the surrogate *is* the chat id — the one
    place the two spellings are knowingly confused, per the identity rule in
    core/ports/users.py."""
    _run(FakeSource([_order()]), writes)
    assert writes["user_ids"] == [CHAT]


def test_the_orders_are_written_against_the_same_id_the_unit_was_opened_for(writes):
    """A unit opened for one person and written for another is the failure the
    surrogate exists to make impossible; here they must not drift apart."""
    _run(FakeSource([_order()]), writes)
    [(written_for, _rows)] = writes["orders"]
    assert written_for == writes["user_ids"][0]


def test_the_profile_write_carries_no_phone(writes):
    """It used to call save_user with the number this function was handed.
    update_profile refuses one by design — enrichment must never be able to
    change whose orders a chat can see — so the adapter reads it back instead.
    A phone arriving here again would mean that guarantee had been undone."""
    keycrm = FakeSource([_order(buyer_name="Тесто-Клієнт")])
    _run(keycrm, writes)
    [(_chat, fields)] = writes["profiles"]
    assert set(fields) == {"full_name", "email"}


def test_the_scenario_never_binds_a_number(writes):
    """bind_phone is the one write that changes who a chat is, and a sync is not
    it. The fake raises rather than records, so a future edit that reached for
    it would fail here rather than in production."""
    _run(FakeSource([_order(buyer_name="Тесто-Клієнт", buyer_email="t@e.com")]),
         writes)  # does not raise


def test_the_directory_is_a_real_customer_directory(writes):
    """Both sides of the seam satisfy it, or the fake is testing a fiction."""
    assert isinstance(writes["directory"], CustomerDirectory)


# --- the one claim the fakes cannot make ------------------------------------

def test_the_number_survives_the_profile_refresh(tmp_path, monkeypatch):
    """Against the real stack, deliberately, because nothing above can say this.

    The fake profile writer never sees a phone, so it cannot show what happens
    to the stored one. The move took the number out of this call — save_user
    used to be handed it, update_profile refuses one — and underneath it is
    still an INSERT OR REPLACE that rewrites the whole row. If the adapter's
    read-it-back were ever dropped, every synced customer would lose the number
    that decides whose orders they can see, and every test above would still
    pass.
    """
    from core.domain.phone import verified_phone
    from core.repos import base as repos_base
    from core.repos.schema import init_db
    from core.repos.uow import SqliteUnitOfWork
    from core.repos.users import SqliteCustomerDirectory, get_user

    monkeypatch.setattr(repos_base, "DB_PATH", str(tmp_path / "bot_data.db"))
    phone = verified_phone(raw_number=PHONE, contact_user_id=CHAT, sender_user_id=CHAT)

    async def scenario():
        await init_db()
        async with SqliteUnitOfWork(user_id=CHAT) as uow:
            await uow.users.bind_phone(CHAT, phone)
            await uow.commit()
        keycrm = FakeSource([_order(buyer_name="Тесто-Клієнт",
                                    buyer_email="t@example.com")])
        await sync_orders(CHAT, PHONE, keycrm, SqliteCustomerDirectory(),
                          SqliteUnitOfWork)
        return await get_user(CHAT)

    user = asyncio.run(scenario())
    assert user["phone"] == PHONE, "the sync must not be able to lose the number"
    assert (user["full_name"], user["email"]) == ("Тесто-Клієнт", "t@example.com")
