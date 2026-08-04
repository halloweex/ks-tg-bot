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
from core.repos import base as repos_base
from core.repos import sync_state
from core.repos.orders import get_cached_orders
from core.repos.schema import init_db
from core.repos.users import save_user
from core.usecases.sync_incremental import (OVERLAP, RECONCILE_EVERY,
                                            RECONCILE_WINDOW, SOURCE, plan_window,
                                            route, sync_changed_orders)

NOW = datetime(2026, 8, 4, 16, 20, 0, tzinfo=timezone.utc)
CHAT = 555
PHONE = "+380670000000"


def _stamp_of(moment: datetime) -> str:
    """The one format both the CRM filter and SQLite's datetime() speak."""
    return moment.strftime("%Y-%m-%d %H:%M:%S")


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
    state = {"cursor": "2026-08-04 16:18:00",
             "last_full_at": "2026-08-04 10:00:00"}
    start, _end, full = plan_window(state, NOW)
    assert start == datetime(2026, 8, 4, 16, 18, tzinfo=timezone.utc) - OVERLAP
    assert full is False


def test_the_reconciliation_comes_back_once_a_week():
    state = {"cursor": "2026-08-04 16:18:00",
             "last_full_at": _stamp_of(NOW - RECONCILE_EVERY - timedelta(minutes=1))}
    _start, _end, full = plan_window(state, NOW)
    assert full is True


def test_an_unreadable_timestamp_widens_the_window_rather_than_raising():
    """The safe answer to "I cannot tell how far back to read" is further back:
    re-reading costs a request, not re-reading costs the orders in the gap."""
    start, _end, full = plan_window({"cursor": "yesterday-ish"}, NOW)
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

    result = asyncio.run(sync_changed_orders(crm, now=NOW))

    assert result.fetched == 1 and result.written == 1
    assert [row["source_order_id"] for row in asyncio.run(get_cached_orders(CHAT))] == ["900001"]
    assert asyncio.run(sync_state.get_state(SOURCE))["cursor"] == _stamp_of(NOW)


def test_a_failed_sweep_leaves_the_cursor_where_it_was(db):
    _register()
    asyncio.run(sync_state.finish_success(SOURCE, "2026-08-04 16:00:00"))

    with pytest.raises(RuntimeError):
        asyncio.run(sync_changed_orders(BrokenCRM(), now=NOW))

    state = asyncio.run(sync_state.get_state(SOURCE))
    assert state["cursor"] == "2026-08-04 16:00:00"
    assert state["last_error"] == "RuntimeError: the CRM is down"


def test_a_sweep_that_cannot_write_does_not_report_success(db, monkeypatch):
    """The failure between reading and writing. Recording success here would
    move the cursor past orders that were fetched and then dropped on the floor.
    """
    from core.usecases import sync_incremental

    async def broken_upsert(chat_id: int, rows: list) -> None:
        raise RuntimeError("database is locked")

    _register()
    monkeypatch.setattr(sync_incremental, "upsert_orders", broken_upsert)

    with pytest.raises(RuntimeError):
        asyncio.run(sync_changed_orders(FakeCRM([_order()]), now=NOW))

    assert asyncio.run(sync_state.get_state(SOURCE))["cursor"] is None


def test_an_empty_window_is_a_success(db):
    """Most two-minute windows are empty, and an empty one is exactly the case
    where the cursor must still move — otherwise a quiet night makes the next
    sweep read from the last order rather than from the last check."""
    result = asyncio.run(sync_changed_orders(FakeCRM(), now=NOW))
    assert result.fetched == 0
    assert asyncio.run(sync_state.get_state(SOURCE))["cursor"] == _stamp_of(NOW)


def test_the_first_sweep_asks_for_the_reconciliation_window(db):
    crm = FakeCRM()
    asyncio.run(sync_changed_orders(crm, now=NOW))
    assert crm.windows == [(_stamp_of(NOW - RECONCILE_WINDOW), _stamp_of(NOW))]


def test_the_second_sweep_asks_from_the_cursor(db):
    crm = FakeCRM()
    asyncio.run(sync_changed_orders(crm, now=NOW))
    later = NOW + timedelta(minutes=2)
    asyncio.run(sync_changed_orders(crm, now=later))

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
    asyncio.run(sync_changed_orders(crm, now=NOW))
    later = datetime.now(timezone.utc) + RECONCILE_EVERY + timedelta(minutes=1)
    asyncio.run(sync_changed_orders(crm, now=later))

    assert crm.windows[1] == (_stamp_of(later - RECONCILE_WINDOW), _stamp_of(later))


def test_the_sweep_resolves_a_chat_nobody_has_looked_up_yet(db):
    """A customer registered before the map existed is invisible to the sweep
    until something asks the CRM by their number. This is that ask, and it
    happens before the window is read so the chat is routed the same round."""
    from core.repos.users import registered_buyers

    _register()
    lookup = FakeLookup([_order(order_id=44730, phone="+380670007962")])
    crm = FakeCRM([_order(order_id=44731, phone="+380670007962")])

    result = asyncio.run(sync_changed_orders(crm, lookup=lookup, now=NOW))

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
    asyncio.run(sync_changed_orders(FakeCRM(), lookup=lookup, now=NOW))
    asyncio.run(sync_changed_orders(FakeCRM(), lookup=lookup, now=NOW))
    assert lookup.asked == [PHONE]


def test_a_failed_lookup_does_not_cost_the_sweep_its_window(db):
    """Resolution is a nicety; the window is the reason the sweep exists."""
    _register()

    class BrokenLookup:
        async def get_orders_by_phone(self, phone: str) -> list:
            raise RuntimeError("the CRM is down")

    result = asyncio.run(
        sync_changed_orders(FakeCRM(), lookup=BrokenLookup(), now=NOW))
    assert result.fetched == 0
    assert asyncio.run(sync_state.get_state(SOURCE))["cursor"] == _stamp_of(NOW)


def test_an_order_for_a_stranger_costs_no_write(db):
    """Fetched and dropped, and the two numbers are reported separately: the gap
    between them is how much of the business is not in the bot yet."""
    _register()
    result = asyncio.run(
        sync_changed_orders(
            FakeCRM([_order(phone="+380990000000", buyer="99999")]), now=NOW)
    )
    assert (result.fetched, result.written) == (1, 0)
    assert asyncio.run(get_cached_orders(CHAT)) == []
