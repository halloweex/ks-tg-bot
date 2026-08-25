"""The same scenario through both units, and the things only the real one can do.

Point 3 of docs/postgres-migration.md asks for exactly this: "тесты агрегатов
гоняются против обеих: одинаковый результат на одном и том же сценарии — это и
есть доказательство, что подмена корректна". A test that only ever sees one
engine cannot notice that the two disagree, and disagreeing quietly is the whole
risk of a second implementation.

Postgres has no published port (Rule 12 of the migration charter), so these skip
unless TEST_DATABASE_URL is set — reachable from inside the docker network, or
over an SSH tunnel. The SQLite half runs everywhere and always.
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone

import pytest

from core.domain.order import Order, order_row
from core.domain.phone import verified_phone
from core.ports.repositories import OrderCache, UnitOfWork, UserProfiles
from core.repos import base as repos_base
from core.repos.orders import get_cached_orders
from core.repos.schema import init_db
from core.repos.uow import SqliteUnitOfWork
from core.repos.users import get_user

DSN = os.getenv("TEST_DATABASE_URL", "")
pg_only = pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL not set")

CHAT = 555
PHONE = verified_phone(raw_number="+380670000000", contact_user_id=1, sender_user_id=1)
ORDER = Order(source="keycrm", source_order_id="900001", status_name="delivered",
              grand_total=1450.0, currency="грн", ordered_at="2026-07-14T09:12:33")


# ── the shared scenario ──────────────────────────────────────────────────────
async def _scenario(make_uow, chat: int) -> int:
    """Register, enrich, and cache one order. Returns the person's id.

    Written once and run twice. It never names an engine, which is the point:
    if it needed to, the port would not be doing its job.
    """
    async with make_uow(user_id=None) as uow:
        user_id = await uow.users.bind_phone(chat, PHONE)
        await uow.commit()

    async with make_uow(user_id=user_id) as uow:
        await uow.users.update_profile(user_id, full_name="Тесто-Клієнт",
                                       email="t@example.com")
        await uow.orders.upsert(user_id, [order_row(ORDER, chat)])
        await uow.commit()
    return user_id


# ── SQLite side ──────────────────────────────────────────────────────────────
@pytest.fixture()
def sqlite_db(tmp_path, monkeypatch):
    monkeypatch.setattr(repos_base, "DB_PATH", str(tmp_path / "bot_data.db"))
    asyncio.run(init_db())


def test_sqlite_runs_the_scenario_and_returns_the_chat_as_the_id(sqlite_db):
    async def run():
        user_id = await _scenario(lambda **kw: SqliteUnitOfWork(**kw), CHAT)
        return user_id, await get_user(CHAT), await get_cached_orders(CHAT)

    user_id, profile, orders = asyncio.run(run())
    assert user_id == CHAT, "here the natural key is the identity"
    assert profile == {"phone": PHONE.e164, "full_name": "Тесто-Клієнт",
                       "email": "t@example.com"}
    assert [o["source_order_id"] for o in orders] == ["900001"]
    assert orders[0]["merge_key"] == "keycrm:900001"


# ── Postgres side ────────────────────────────────────────────────────────────
def with_pool(body):
    """Create the pool, run `body(pool)`, close it — all inside ONE event loop.

    Not a fixture, and that is the point. asyncpg binds a pool to the loop that
    created it, while this project runs each test in its own `asyncio.run`
    (tests/test_outbox_repo.py and everything around it). A pool built in a
    fixture therefore belongs to a loop that is already closed by the time the
    test body runs, and every call fails with "attached to a different loop" —
    a property of the harness, not of the code under test. Learned by writing it
    the other way first.

    Truncating on the way in rather than the way out: a failed test leaves its
    rows behind to be looked at.
    """
    import asyncpg

    async def outer():
        pool = await asyncpg.create_pool(DSN, min_size=1, max_size=4)
        try:
            async with pool.acquire() as c:
                await c.execute("TRUNCATE orders, users RESTART IDENTITY CASCADE")
            return await body(pool)
        finally:
            await pool.close()

    return asyncio.run(outer())


@pg_only
def test_the_pg_implementation_satisfies_the_port():
    from core.repos.pg import SqlUnitOfWork

    async def body(pool):
        uow = SqlUnitOfWork(pool)
        assert isinstance(uow, UnitOfWork)
        async with uow:
            assert isinstance(uow.orders, OrderCache)
            assert isinstance(uow.users, UserProfiles)

    with_pool(body)


@pg_only
def test_postgres_runs_the_same_scenario_and_mints_an_id_of_its_own():
    """The assertion that could not be made before: the id is the database's,
    not the chat's, and the caller could not have guessed it."""
    from core.repos.pg import unit_of_work_factory

    async def body(pool):
        user_id = await _scenario(unit_of_work_factory(pool), CHAT)
        async with pool.acquire() as c:
            profile = await c.fetchrow(
                "SELECT tg_chat_id, phone_normalized, full_name, email "
                "FROM users WHERE id = $1", user_id)
            orders = await c.fetch(
                "SELECT source_order_id, merge_key FROM orders WHERE user_id = $1",
                user_id)
        return user_id, profile, orders

    user_id, profile, orders = with_pool(body)

    assert user_id != CHAT, "a surrogate, minted by the sequence"
    assert profile["tg_chat_id"] == CHAT
    assert profile["phone_normalized"] == PHONE.e164
    assert profile["full_name"] == "Тесто-Клієнт"
    assert profile["email"] == "t@example.com"
    assert [o["source_order_id"] for o in orders] == ["900001"]
    assert orders[0]["merge_key"] == "keycrm:900001", "derived, never accepted"


@pg_only
def test_the_link_that_brought_a_customer_is_write_once_here_too():
    """The rule both engines have to keep, asserted against the one that does it
    in a different statement.

    SQLite spells it `COALESCE(NULLIF((SELECT source …), ''), ?)` inside an
    INSERT OR REPLACE; Postgres spells it
    `COALESCE(NULLIF(users.source, ''), EXCLUDED.source)` inside an ON CONFLICT.
    Two spellings of one rule is exactly the shape that drifts, and the
    conformance test cannot see it — it compares signatures, and both accept a
    `source` either way.

    The case that matters is the second bind: a customer re-verifying their
    number must not be re-attributed to whoever they last clicked.
    """
    from core.repos.pg import unit_of_work_factory

    async def body(pool):
        make = unit_of_work_factory(pool)
        async with make(user_id=None) as uow:
            user_id = await uow.users.bind_phone(CHAT, PHONE, source="ref_777")
            await uow.commit()
        async with make(user_id=user_id) as uow:
            await uow.users.bind_phone(CHAT, PHONE, source="ref_999")
            await uow.commit()
        async with make(user_id=user_id) as uow:
            await uow.users.bind_phone(CHAT, PHONE)
            await uow.commit()
        async with pool.acquire() as c:
            return await c.fetchval("SELECT source FROM users WHERE id = $1", user_id)

    assert with_pool(body) == "ref_777"


@pg_only
def test_a_customer_who_arrived_alone_is_attributed_to_nobody_here_either():
    """The column is NOT NULL DEFAULT '' in revision 001, so empty is the only
    thing "not yet answered" can be — and offering empty must leave it that
    way rather than writing a link nobody clicked."""
    from core.repos.pg import unit_of_work_factory

    async def body(pool):
        make = unit_of_work_factory(pool)
        async with make(user_id=None) as uow:
            user_id = await uow.users.bind_phone(CHAT, PHONE)
            await uow.commit()
        async with pool.acquire() as c:
            return await c.fetchval("SELECT source FROM users WHERE id = $1", user_id)

    assert with_pool(body) == ""


@pg_only
def test_the_naive_timestamp_is_stored_as_utc():
    """Pinned because revision 001 chose timestamptz without saying which zone
    the naive strings arrive in. The data transfer (point 4) must apply this same
    rule to history, and a test is how that stays true."""
    from core.repos.pg import unit_of_work_factory

    async def body(pool):
        user_id = await _scenario(unit_of_work_factory(pool), CHAT)
        async with pool.acquire() as c:
            return await c.fetchval(
                "SELECT ordered_at FROM orders WHERE user_id = $1", user_id)

    assert with_pool(body) == datetime(2026, 7, 14, 9, 12, 33, tzinfo=timezone.utc)


@pg_only
def test_leaving_the_block_early_rolls_back():
    """The limitation the SQLite unit documents about itself, gone.

    tests/test_unit_of_work.py pins the opposite for the shim and says so
    deliberately. This is the test that had to exist before any scenario is
    allowed to depend on rollback."""
    from core.repos.pg import unit_of_work_factory

    async def body(pool):
        make = unit_of_work_factory(pool)
        with pytest.raises(RuntimeError):
            async with make(user_id=None) as uow:
                await uow.users.bind_phone(CHAT, PHONE)
                raise RuntimeError("died before commit")
        async with pool.acquire() as c:
            return await c.fetchval("SELECT count(*) FROM users")

    assert with_pool(body) == 0, "nothing survived, unlike the shim"


@pg_only
def test_the_same_number_from_another_chat_raises_rather_than_guessing():
    """A person changing Telegram account is a linking decision (§4.8), not
    something an upsert may infer. SQLite would silently have created a second
    person holding the same number; here it is refused."""
    import asyncpg as _asyncpg

    from core.repos.pg import unit_of_work_factory

    async def body(pool):
        make = unit_of_work_factory(pool)
        async with make(user_id=None) as uow:
            await uow.users.bind_phone(CHAT, PHONE)
            await uow.commit()
        with pytest.raises(_asyncpg.UniqueViolationError):
            async with make(user_id=None) as uow:
                await uow.users.bind_phone(CHAT + 1, PHONE)
                await uow.commit()

    with_pool(body)


@pg_only
def test_enriching_a_user_who_does_not_exist_creates_nobody():
    from core.repos.pg import unit_of_work_factory

    async def body(pool):
        make = unit_of_work_factory(pool)
        async with make(user_id=999999) as uow:
            await uow.users.update_profile(999999, full_name="Никто")
            await uow.commit()
        async with pool.acquire() as c:
            return await c.fetchval("SELECT count(*) FROM users")

    assert with_pool(body) == 0


@pg_only
def test_the_identity_is_transaction_local():
    """`SET LOCAL` and not `SET`: it must die with the transaction, or a pooled
    connection hands one person's identity to the next borrower."""
    from core.repos.pg import unit_of_work_factory

    async def body(pool):
        async with unit_of_work_factory(pool)(user_id=4242) as uow:
            inside = await uow._conn.fetchval(
                "SELECT current_setting('app.user_id', true)")
        async with pool.acquire() as c:
            after = await c.fetchval("SELECT current_setting('app.user_id', true)")
        return inside, after

    inside, after = with_pool(body)
    assert inside == "4242"
    assert after in (None, ""), "it did not survive the transaction"
