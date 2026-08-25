"""The sweep: which window it reads, whose orders it keeps, when the cursor moves.

Two of these are about losing orders permanently rather than temporarily, and
they are the reason the module exists in this shape. A cursor that advances
after a failed sweep puts a window behind it forever, because nothing asks for a
range the cursor has passed. A cursor that starts at "now" on a database that
has never been swept does the same to every order that already existed.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from core.domain.order import Order
from core.domain.sync import SyncState
from core.repos import base as repos_base
from core.repos import sync_state
from core.repos.orders import get_cached_orders
from core.repos.schema import init_db
from core.repos.sync_state import SqliteSyncJournal
from core.repos.uow import SqliteUnitOfWork
from core.repos.users import SqliteCustomerDirectory, save_user
from core.usecases.sync_incremental import (OVERLAP, RECONCILE_EVERY,
                                            RECONCILE_WINDOW, RETRY_AFTER_HOURS,
                                            SOURCE, plan_window, route,
                                            sync_changed_orders)

NOW = datetime(2026, 8, 4, 16, 20, 0, tzinfo=timezone.utc)
CHAT = 555
PHONE = "+380670000000"


def _stamp_of(moment: datetime) -> str:
    """The one format both the CRM filter and SQLite's datetime() speak."""
    return moment.strftime("%Y-%m-%d %H:%M:%S")


def _sweep(crm, *, lookup=None, now=NOW, unit=SqliteUnitOfWork):
    """One sweep against the real SQLite side of all three storage ports.

    None of them is faked by default: what these tests are about is which rows
    end up in two tables and where the cursor stops, so a fake would be a fake
    of the subject. `unit` is a parameter only because one test needs a write
    that fails.
    """
    return asyncio.run(sync_changed_orders(
        crm, SqliteSyncJournal(), SqliteCustomerDirectory(), unit,
        lookup=lookup, now=now,
    ))


def _state(cursor: str | None = None, last_full_at: str | None = None) -> SyncState:
    """A state as the journal would hand it over: moments, not strings."""
    from core.domain.sync import read_stamp

    return SyncState(source=SOURCE, cursor=read_stamp(cursor),
                     last_full_at=read_stamp(last_full_at))


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(repos_base, "DB_PATH", str(tmp_path / "bot_data.db"))
    asyncio.run(init_db())


class FakeCRM:
    """A changed-orders feed that answers with a fixed list."""

    def __init__(self, orders: list | None = None) -> None:
        self.windows: list[tuple[str, str]] = []
        self._orders = orders or []

    async def get_orders_changed_between(self, start: str, end: str) -> list:
        self.windows.append((start, end))
        return list(self._orders)


class FakeLookup:
    """The same CRM asked the other way round: by number."""

    def __init__(self, orders: list | None = None) -> None:
        self.asked: list[str] = []
        self._orders = orders or []

    async def get_orders_by_phone(self, phone: str) -> list:
        self.asked.append(phone)
        return list(self._orders)


class BrokenCRM:
    async def get_orders_changed_between(self, start: str, end: str) -> list:
        raise RuntimeError("the CRM is down")


BUYER = "6477"


def _order(order_id: int = 900001, *, phone: str = PHONE, buyer: str = BUYER,
           **kw) -> Order:
    return Order(
        source="keycrm", source_order_id=str(order_id), buyer_phone=phone,
        buyer_id=buyer, status_name="delivered", status_group_id=1,
        grand_total=1450.0, currency="грн", ordered_at="2026-07-14T09:12:33", **kw,
    )


def _register(chat_id: int = CHAT, phone: str = PHONE) -> None:
    asyncio.run(save_user(chat_id, phone))


# --- which window -----------------------------------------------------------

def test_a_database_that_was_never_swept_reads_the_reconciliation_window():
    """Not "from now". The customers who registered before the sync existed have
    orders older than this moment, and a cursor starting here never asks for
    them — they would sit unfetched until somebody opened the orders screen."""
    start, end, full = plan_window(None, NOW)
    assert start == NOW - RECONCILE_WINDOW
    assert end == NOW
    assert full is True


def test_a_normal_sweep_starts_before_the_cursor():
    """§5.2. With a strict `>` the records written during the previous pass fall
    between the two windows and are never read."""
    state = _state(cursor="2026-08-04 16:18:00", last_full_at="2026-08-04 10:00:00")
    start, _end, full = plan_window(state, NOW)
    assert start == datetime(2026, 8, 4, 16, 18, tzinfo=timezone.utc) - OVERLAP
    assert full is False


def test_the_reconciliation_comes_back_once_a_week():
    state = _state(cursor="2026-08-04 16:18:00",
                   last_full_at=_stamp_of(NOW - RECONCILE_EVERY - timedelta(minutes=1)))
    _start, _end, full = plan_window(state, NOW)
    assert full is True


def test_a_cursor_that_could_not_be_read_widens_the_window_rather_than_raising():
    """The safe answer to "I cannot tell how far back to read" is further back:
    re-reading costs a request, not re-reading costs the orders in the gap.

    Since commit 21 the deciding happens once, in the adapter — an unparseable
    column arrives here as None. That the adapter really does answer None for a
    damaged cursor is pinned in tests/test_sync_state.py; this is the half that
    says what the sweep then does with it."""
    start, _end, full = plan_window(_state(cursor=None), NOW)
    assert start == NOW - RECONCILE_WINDOW
    assert full is True


# --- whose orders -----------------------------------------------------------

def test_an_order_is_routed_to_the_chat_holding_its_number():
    rows = route([_order()], [(CHAT, PHONE)])
    assert list(rows) == [CHAT]
    assert rows[CHAT][0]["source_order_id"] == "900001"
    assert rows[CHAT][0]["chat_id"] == CHAT


def test_the_number_is_matched_normalised_on_both_sides():
    """The two sides are typed by different people into different systems: a
    manager writing 0670000000 in the CRM and Telegram handing over
    +380670000000 are the same customer."""
    rows = route([_order(phone="0670000000")], [(CHAT, "+380670000000")])
    assert list(rows) == [CHAT]


def test_an_order_belonging_to_nobody_registered_is_dropped():
    """Most of what the sweep reads. Three people have registered; twenty
    thousand have bought something."""
    assert route([_order(phone="+380990000000", buyer="99999")],
                 [(CHAT, PHONE)], [(CHAT, BUYER)]) == {}


def test_an_order_without_a_number_matches_nobody():
    """Rather than matching the users whose number also failed to normalize."""
    assert route([_order(phone="", buyer="")], [(CHAT, "")]) == {}


# --- the card, which is what actually routes ---------------------------------

def test_an_order_is_routed_by_the_buyer_card_when_the_number_does_not_match():
    """The production defect, in one test. A buyer card holds several numbers,
    the order carries one of them, and the CRM's by-number search matches all —
    so the only registered customer there had 25 orders, every one found by
    their number and none of them carrying it. Routing by number wrote nothing,
    silently."""
    orders = [_order(phone="+380670007962")]          # the card's other number
    assert route(orders, [(CHAT, PHONE)]) == {}
    assert list(route(orders, [(CHAT, PHONE)], [(CHAT, BUYER)])) == [CHAT]


def test_a_chat_matching_by_both_gets_the_order_once():
    rows = route([_order()], [(CHAT, PHONE)], [(CHAT, BUYER)])
    assert len(rows[CHAT]) == 1


def test_a_new_card_for_a_known_number_still_routes():
    """Why the number stays as the second rule: a card created after the last
    by-number request is one nothing has mapped yet."""
    rows = route([_order(buyer="70001")], [(CHAT, PHONE)], [(CHAT, BUYER)])
    assert list(rows) == [CHAT]


def test_one_number_on_two_chats_writes_to_both():
    """A household, or a customer who moved to a new Telegram account. Both are
    already visible through the on-demand refresh; this keeps them equal."""
    rows = route([_order()], [(CHAT, PHONE), (556, PHONE)])
    assert sorted(rows) == [CHAT, 556]


# --- the cursor -------------------------------------------------------------

def test_a_successful_sweep_writes_the_orders_and_moves_the_cursor(db):
    _register()
    crm = FakeCRM([_order()])

    result = _sweep(crm)

    assert result.fetched == 1 and result.written == 1
    assert [row["source_order_id"] for row in asyncio.run(get_cached_orders(CHAT))] == ["900001"]
    assert asyncio.run(sync_state.get_state(SOURCE))["cursor"] == _stamp_of(NOW)


def test_a_failed_sweep_leaves_the_cursor_where_it_was(db):
    _register()
    asyncio.run(sync_state.finish_success(SOURCE, "2026-08-04 16:00:00"))

    with pytest.raises(RuntimeError):
        _sweep(BrokenCRM())

    state = asyncio.run(sync_state.get_state(SOURCE))
    assert state["cursor"] == "2026-08-04 16:00:00"
    assert state["last_error"] == "RuntimeError: the CRM is down"


class BrokenUnit:
    """A unit whose order write always fails, and which records how it ended.

    Since commit 21 this is a fake port rather than a patched module attribute,
    and the difference is not cosmetic: patching `sync_incremental.upsert_orders`
    could not notice the scenario calling it with the wrong arguments, and it
    could not see whether the block was committed either.

    `log` is shared with RecordingJournal below so that the *order* of the two
    can be asserted, not merely that both happened.
    """

    def __init__(self, log: list[str] | None = None) -> None:
        self.committed = 0
        self.exited_with: list[type | None] = []
        self.log = log if log is not None else []
        self.orders = self
        self.users = None

    async def upsert(self, user_id: int, rows: list) -> None:
        raise RuntimeError("database is locked")

    async def __aenter__(self) -> "BrokenUnit":
        self.log.append("enter")
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        self.exited_with.append(exc_type)
        self.log.append("exit")

    async def commit(self) -> None:
        self.committed += 1
        self.log.append("commit")


class RecordingJournal:
    """The real journal, with every call written into a shared log first."""

    def __init__(self, log: list[str]) -> None:
        self.log = log
        self._inner = SqliteSyncJournal()

    async def begin(self, source: str) -> None:
        self.log.append("begin")
        await self._inner.begin(source)

    async def finished(self, source, cursor, *, full: bool = False) -> None:
        self.log.append("finished")
        await self._inner.finished(source, cursor, full=full)

    async def failed(self, source: str, error: str) -> None:
        self.log.append("failed")
        await self._inner.failed(source, error)

    async def state(self, source: str):
        return await self._inner.state(source)


def test_a_sweep_that_cannot_write_does_not_report_success(db):
    """The failure between reading and writing. Recording success here would
    move the cursor past orders that were fetched and then dropped on the floor.
    """
    _register()
    broken = BrokenUnit()

    with pytest.raises(RuntimeError):
        _sweep(FakeCRM([_order()]), unit=lambda **kw: broken)

    assert asyncio.run(sync_state.get_state(SOURCE))["cursor"] is None
    assert broken.committed == 0, "a block that raised must not have committed"
    assert broken.exited_with == [RuntimeError], "and must have left through the error"


def test_the_failure_is_recorded_after_the_block_that_failed_is_left(db):
    """A record of what went wrong is worth nothing if it disappears with the
    transaction that went wrong. Under the shim nothing rolls back, so what is
    pinned here is the ordering the real engine will need: the unit is left
    first, and only then is the error written.

    The assertion is the sequence itself. Asserting that both happened would
    hold just as well for the arrangement this test exists to reject — the
    error written from inside the block.
    """
    _register()
    log: list[str] = []
    broken = BrokenUnit(log)

    with pytest.raises(RuntimeError):
        asyncio.run(sync_changed_orders(
            FakeCRM([_order()]), RecordingJournal(log),
            SqliteCustomerDirectory(), lambda **kw: broken, now=NOW))

    assert log == ["begin", "enter", "exit", "failed"]
    assert asyncio.run(sync_state.get_state(SOURCE))["last_error"] == (
        "RuntimeError: database is locked"
    )


def test_a_successful_sweep_commits_the_block_and_then_moves_the_cursor(db):
    """The other half of the same ordering, and the one the happy path relies
    on: the cursor is the promise that the orders are written, so it is made
    after the commit rather than beside it."""
    _register()
    log: list[str] = []

    class Unit(BrokenUnit):
        async def upsert(self, user_id: int, rows: list) -> None:
            self.log.append("upsert")

    unit = Unit(log)
    asyncio.run(sync_changed_orders(
        FakeCRM([_order()]), RecordingJournal(log), SqliteCustomerDirectory(),
        lambda **kw: unit, now=NOW))

    assert log == ["begin", "enter", "upsert", "commit", "exit", "finished"]


def test_the_window_is_written_in_the_service_context(db):
    """`user_id=None` is what the UnitOfWork port calls the worker acting for
    nobody, and this sweep is the only scenario it fits: the window belongs to
    no one, and its rows go to whichever chats they match. Opening it for a
    person would be a claim the sweep cannot make."""
    _register()
    opened: list[int | None] = []

    def factory(*, user_id: int | None = None):
        opened.append(user_id)
        return SqliteUnitOfWork(user_id=user_id)

    _sweep(FakeCRM([_order()]), unit=factory)
    assert opened == [None]


def test_an_empty_window_is_a_success(db):
    """Most two-minute windows are empty, and an empty one is exactly the case
    where the cursor must still move — otherwise a quiet night makes the next
    sweep read from the last order rather than from the last check."""
    result = _sweep(FakeCRM())
    assert result.fetched == 0
    assert asyncio.run(sync_state.get_state(SOURCE))["cursor"] == _stamp_of(NOW)


def test_the_first_sweep_asks_for_the_reconciliation_window(db):
    crm = FakeCRM()
    _sweep(crm)
    assert crm.windows == [(_stamp_of(NOW - RECONCILE_WINDOW), _stamp_of(NOW))]


def test_the_second_sweep_asks_from_the_cursor(db):
    crm = FakeCRM()
    _sweep(crm)
    later = NOW + timedelta(minutes=2)
    _sweep(crm, now=later)

    assert crm.windows[1] == (_stamp_of(NOW - OVERLAP), _stamp_of(later))


def test_a_week_later_the_full_window_comes_back(db):
    """Measured from the real clock, because the row is stamped by the database.

    `last_full_at` is written as SQLite datetime('now'); the decision to run a
    reconciliation compares it against the process clock. The two are the same
    UTC clock in production, so the only place the difference shows is a test
    that pretends today is some other day — which is why the second sweep here
    is a week past *now* rather than a week past NOW.
    """
    crm = FakeCRM()
    _sweep(crm)
    later = datetime.now(timezone.utc) + RECONCILE_EVERY + timedelta(minutes=1)
    _sweep(crm, now=later)

    assert crm.windows[1] == (_stamp_of(later - RECONCILE_WINDOW), _stamp_of(later))


def test_the_sweep_resolves_a_chat_nobody_has_looked_up_yet(db):
    """A customer registered before the map existed is invisible to the sweep
    until something asks the CRM by their number. This is that ask, and it
    happens before the window is read so the chat is routed the same round."""
    from core.repos.users import registered_buyers

    _register()
    lookup = FakeLookup([_order(order_id=44730, phone="+380670007962")])
    crm = FakeCRM([_order(order_id=44731, phone="+380670007962")])

    result = _sweep(crm, lookup=lookup)

    assert lookup.asked == [PHONE]
    assert asyncio.run(registered_buyers()) == [(CHAT, BUYER)]
    # Both: the one the lookup wrote and the one the window then routed.
    assert result.written == 1
    assert sorted(row["source_order_id"]
                  for row in asyncio.run(get_cached_orders(CHAT))) == ["44730", "44731"]


def test_a_chat_already_mapped_is_not_looked_up_again(db):
    """One request per customer, once — not one per sweep."""
    _register()
    lookup = FakeLookup([_order(order_id=44730)])
    _sweep(FakeCRM(), lookup=lookup)
    _sweep(FakeCRM(), lookup=lookup)
    assert lookup.asked == [PHONE]


def test_a_customer_the_crm_has_never_heard_of_is_asked_once_a_day(db):
    """Two of the three chats in production are this: registered, no orders in
    the CRM, therefore no card to find. Without remembering the attempt they
    would be looked up on every sweep, forever, and the log line would keep
    claiming it resolved them."""
    _register()
    lookup = FakeLookup([])                      # the CRM knows nobody by that number

    _sweep(FakeCRM(), lookup=lookup)
    _sweep(FakeCRM(), lookup=lookup)

    assert lookup.asked == [PHONE]


def test_the_day_after_they_are_asked_again(db):
    """Because "has never ordered" is a state customers leave.

    Asserted through the sweep rather than by calling the repository, and that
    is the point since commit 21: the window is RETRY_AFTER_HOURS in the
    scenario now, and the repository's own default no longer decides anything
    here. A version of this test that called `chats_without_crm_buyer()`
    directly would keep passing if the constant were edited to ten days.

    **The hours below are literals on purpose.** Writing them as
    RETRY_AFTER_HOURS ± 1 was the first attempt and it pinned nothing: both ages
    move with the constant, so a day silently becoming ten days stays green.
    A test whose fixture is derived from the value under test measures only that
    the code is consistent with itself.

    The stamp is aged by hand: it is written by the database clock, and the
    alternative to reaching for it here is a test that waits a day.
    """
    import aiosqlite

    from core.repos import base as repos_base

    _register()
    lookup = FakeLookup([])
    _sweep(FakeCRM(), lookup=lookup)
    _sweep(FakeCRM(), lookup=lookup)
    assert lookup.asked == [PHONE], "still inside the window"

    async def age_the_stamp(hours: int) -> None:
        async with aiosqlite.connect(repos_base.DB_PATH) as db_:
            await db_.execute(
                f"UPDATE users SET crm_checked_at = datetime('now', '-{hours} hours')")
            await db_.commit()

    asyncio.run(age_the_stamp(23))
    _sweep(FakeCRM(), lookup=lookup)
    assert lookup.asked == [PHONE], "an hour short of a day is still inside the window"

    asyncio.run(age_the_stamp(25))
    _sweep(FakeCRM(), lookup=lookup)
    assert lookup.asked == [PHONE, PHONE], "an hour past a day, and they are asked again"


def test_the_retry_window_is_a_day(db):
    """Stated once, next to the test above that measures it in literal hours.

    The two together are the pin: this one says which number the scenario is
    supposed to hold, that one says the sweep really behaves that way at 23 and
    25 hours. Either alone can be satisfied by an edit that breaks the other.
    """
    assert RETRY_AFTER_HOURS == 24


def test_a_failed_lookup_does_not_cost_the_sweep_its_window(db):
    """Resolution is a nicety; the window is the reason the sweep exists."""
    _register()

    class BrokenLookup:
        async def get_orders_by_phone(self, phone: str) -> list:
            raise RuntimeError("the CRM is down")

    result = _sweep(FakeCRM(), lookup=BrokenLookup())
    assert result.fetched == 0
    assert asyncio.run(sync_state.get_state(SOURCE))["cursor"] == _stamp_of(NOW)


def test_an_order_for_a_stranger_costs_no_write(db):
    """Fetched and dropped, and the two numbers are reported separately: the gap
    between them is how much of the business is not in the bot yet."""
    _register()
    result = _sweep(FakeCRM([_order(phone="+380990000000", buyer="99999")]))
    assert (result.fetched, result.written) == (1, 0)
    assert asyncio.run(get_cached_orders(CHAT)) == []
