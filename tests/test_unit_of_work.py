"""The SQLite unit of work satisfies the port, and writes what it says it writes.

Structural conformance is checked because the port is a Protocol: nothing
inherits from it, so nothing tells you it drifted except a call that fails at
runtime, in production, on the path that runs least often.

The round trip is checked against a real database file because a shim that
delegates is exactly the kind of code where a wrong argument order compiles and
silently writes the phone into the name column.
"""
from __future__ import annotations

import asyncio

import pytest

from core.domain.phone import verified_phone
from core.ports.repositories import OrderCache, UnitOfWork, UserProfiles
from core.repos import base as repos_base
from core.repos.orders import get_cached_orders
from core.repos.schema import init_db
from core.repos.uow import SqliteUnitOfWork
from core.repos.users import get_user

USER = 555
PHONE = verified_phone(raw_number="+380670000000", contact_user_id=1, sender_user_id=1)


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(repos_base, "DB_PATH", str(tmp_path / "bot_data.db"))
    asyncio.run(init_db())


def test_the_implementation_satisfies_the_port():
    uow = SqliteUnitOfWork()
    assert isinstance(uow, UnitOfWork)
    assert isinstance(uow.orders, OrderCache)
    assert isinstance(uow.users, UserProfiles)


def test_the_service_context_is_spelled_out_rather_than_defaulted_into():
    """None means the worker, acting for nobody. It is the default only because
    every caller that means a person passes one — and that is what the Postgres
    implementation will turn into SET LOCAL app.user_id."""
    assert SqliteUnitOfWork().user_id is None
    assert SqliteUnitOfWork(user_id=USER).user_id == USER


def test_a_profile_written_through_the_unit_comes_back(db):
    async def scenario():
        async with SqliteUnitOfWork(user_id=USER) as uow:
            await uow.users.bind_phone(USER, PHONE)
            await uow.users.update_profile(USER, full_name="Тесто-Клієнт",
                                           email="t@example.com")
            await uow.commit()
        return await get_user(USER)

    assert asyncio.run(scenario()) == {
        "phone": PHONE.e164, "full_name": "Тесто-Клієнт", "email": "t@example.com",
    }


def test_enriching_a_user_who_is_not_bound_yet_creates_nobody(db):
    """The row it would otherwise write is a user with no phone — a person
    nobody can be, and one that would then occupy the chat id."""
    async def scenario():
        async with SqliteUnitOfWork(user_id=USER) as uow:
            await uow.users.update_profile(USER, full_name="Тесто-Клієнт")
        return await get_user(USER)

    assert asyncio.run(scenario()) is None


def test_enrichment_cannot_change_the_number(db):
    """The phone is not a parameter of update_profile rather than an optional
    one nobody passes: the write that changes who a chat is has its own method
    and its own type."""
    import inspect

    from core.repos.uow import SqliteUserProfiles

    params = inspect.signature(SqliteUserProfiles.update_profile).parameters
    assert "phone" not in params


def test_orders_written_through_the_unit_come_back(db):
    from core.domain.order import Order, order_row

    order = Order(source="keycrm", source_order_id="900001", status_name="delivered",
                  grand_total=1450.0, currency="грн", ordered_at="2026-07-14T09:12:33")

    async def scenario():
        async with SqliteUnitOfWork(user_id=USER) as uow:
            await uow.users.bind_phone(USER, PHONE)
            await uow.orders.upsert(USER, [order_row(order, USER)])
            await uow.commit()
        return await get_cached_orders(USER)

    rows = asyncio.run(scenario())
    assert [r["source_order_id"] for r in rows] == ["900001"]
    assert rows[0]["merge_key"] == "keycrm:900001", "derived here, never accepted"


def test_leaving_the_block_early_does_not_roll_back_yet(db):
    """Pinned as a limitation, not as a feature. This implementation delegates
    to functions that commit on their own, so there is nothing to undo — and a
    scenario that relies on rollback would be relying on something that only
    starts working when SqlUnitOfWork arrives. Written down here so that the day
    it changes, this test is what changes with it."""
    async def scenario():
        with pytest.raises(RuntimeError):
            async with SqliteUnitOfWork(user_id=USER) as uow:
                await uow.users.bind_phone(USER, PHONE)
                raise RuntimeError("died before commit")
        return await get_user(USER)

    assert asyncio.run(scenario()) is not None, "the write survived, as documented"
