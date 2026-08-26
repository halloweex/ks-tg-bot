"""The §4.8 refusal: a number several people hold is never linked automatically.

Tests of an absence, which §4.8 asks for in those words — "как и матрица
авторизации в §10, он проверяет отсутствие поведения, а такие тесты после факта
не дописывают". The behaviour that must not happen is a customer opening the bot
and finding somebody else's purchase history: their names, their addresses, what
they bought.

Not hypothetical, and that is why the rule is absolute rather than windowed.
Measured against the production CRM on 2026-08-03: 203 numbers belong to more
than one buyer card, 180 of those pairs have orders on both sides. The section
was written about operators reissuing numbers — a future risk — and the
measurement turned it into a rule about data that is already in the CRM and will
arrive with the backfill.

Both doors are covered here because there are two and they are the same shape:
registration links by number, and so does the on-demand refresh behind the
orders screen.
"""
from __future__ import annotations

import asyncio

import pytest

from core.domain.linking import buyer_cards, shared_by_several_people
from core.domain.order import Order
from core.domain.phone import verified_phone
from core.usecases.register import register_customer
from core.usecases.sync_orders import sync_orders

CHAT = 555
NUMBER = "+380670000000"
PHONE = verified_phone(raw_number=NUMBER, contact_user_id=1, sender_user_id=1)

MINE = "6477"
SOMEBODY_ELSE = "9001"


def _order(order_id: int, buyer: str, **kw) -> Order:
    return Order(
        source="keycrm", source_order_id=str(order_id), buyer_id=buyer,
        buyer_phone=NUMBER, status_name="delivered", status_group_id=1,
        grand_total=1450.0, currency="грн", ordered_at="2026-07-14T09:12:33", **kw,
    )


# --- the rule itself --------------------------------------------------------

def test_one_card_is_one_person():
    assert shared_by_several_people([_order(1, MINE), _order(2, MINE)]) is False


def test_two_cards_are_two_people():
    assert shared_by_several_people([_order(1, MINE), _order(2, SOMEBODY_ELSE)]) is True


def test_orders_with_no_buyer_at_all_are_not_a_second_person():
    """A CRM data problem, not an identity one. Counting a blank as its own card
    would deny a real customer their own history for somebody's missing field."""
    assert shared_by_several_people([_order(1, MINE), _order(2, "")]) is False
    assert buyer_cards([_order(1, ""), _order(2, "")]) == set()


def test_nothing_at_all_is_not_a_refusal():
    assert shared_by_several_people([]) is False


# --- the doors --------------------------------------------------------------

class SharedNumberCRM:
    """The CRM answering a by-number search for a number two people hold.

    Which is what it does: the search matches every card carrying the number,
    so one request returns both people's orders and the bot cannot tell from
    the payload which are whose.
    """

    def __init__(self) -> None:
        self.orders = [_order(44730, MINE), _order(44731, SOMEBODY_ELSE)]

    async def get_orders_by_phone(self, phone: str) -> list:
        return list(self.orders)

    async def get_buyer_by_phone(self, phone: str) -> dict | None:
        return None


class Recorder:
    """Every write the two scenarios can make, and none of them permitted."""

    def __init__(self) -> None:
        self.remembered: list = []
        self.written: list = []
        self.profiles: list = []
        self.bound: list = []
        self.shared: list = []

    # CustomerDirectory
    async def remember(self, chat_id: int, buyer_ids: set[str]) -> None:
        self.remembered.append((chat_id, sorted(buyer_ids)))

    async def buyers(self):
        return []

    async def phones(self):
        return []

    async def unidentified(self, *, retry_after_hours: int):
        return []

    async def mark_asked(self, chat_id: int) -> None:
        return None

    async def mark_shared(self, chat_id: int) -> None:
        self.shared.append(chat_id)

    # OrderCache
    async def upsert(self, user_id: int, rows: list) -> None:
        self.written.append((user_id, rows))

    # UserProfiles
    async def bind_phone(self, chat_id: int, phone, *, source: str = "") -> int:
        self.bound.append(chat_id)
        return chat_id

    async def update_profile(self, user_id: int, *, full_name=None, email=None) -> None:
        self.profiles.append((user_id, full_name, email))

    # UnitOfWork
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def commit(self) -> None:
        return None

    @property
    def users(self):
        return self

    @property
    def orders(self):
        return self


@pytest.fixture()
def writes():
    return Recorder()


def _factory(recorder: Recorder):
    def make(*, user_id: int | None = None):
        return recorder

    return make


def test_the_orders_screen_refuses_to_link_a_shared_number(writes):
    """The on-demand refresh. The customer sees an empty list rather than a
    stranger's, and an empty list is a question they can ask about."""
    asyncio.run(sync_orders(CHAT, NUMBER, SharedNumberCRM(), writes,
                            _factory(writes)))

    assert writes.written == [], "not one of these orders may be attached"
    assert writes.profiles == [], "nor a name and an email off a stranger's card"


def test_registration_refuses_to_link_a_shared_number(writes):
    """The mechanism §4.8 calls the most dangerous in the document. A verified
    number proves the number is theirs; it does not prove the history is."""
    asyncio.run(register_customer(CHAT, PHONE, SharedNumberCRM(), writes,
                                  _factory(writes)))

    assert writes.written == []
    assert writes.bound == [CHAT], "they are still registered — only unlinked"


@pytest.mark.parametrize("run", [
    pytest.param(
        lambda w: asyncio.run(sync_orders(CHAT, NUMBER, SharedNumberCRM(), w,
                                          _factory(w))),
        id="orders-screen"),
    pytest.param(
        lambda w: asyncio.run(register_customer(CHAT, PHONE, SharedNumberCRM(), w,
                                                _factory(w))),
        id="registration"),
])
def test_the_buyer_cards_are_not_recorded_either(writes, run):
    """The half that outlives the request, and the reason the guard sits before
    `remember` rather than before the upsert.

    Recording the cards attaches both people to this chat permanently, and the
    window sweep routes by card — so the leak would continue on its own, every
    two minutes, long after this lookup is over. Refusing the orders while
    keeping the map would be a fix that undoes itself.
    """
    run(writes)
    assert writes.remembered == []
    assert writes.shared == [CHAT], (
        "and the refusal is recorded, or the window sweep resumes it by number"
    )


def test_a_number_only_one_person_holds_still_links(writes):
    """The other half of the rule, and the one that keeps it a rule rather than
    a switch: 203 numbers of 18 884 are shared, and the other 18 681 customers
    must still get their orders."""
    class MineOnly(SharedNumberCRM):
        def __init__(self) -> None:
            self.orders = [_order(44730, MINE), _order(44731, MINE)]

    asyncio.run(sync_orders(CHAT, NUMBER, MineOnly(), writes, _factory(writes)))

    assert [r["source_order_id"] for _u, rows in writes.written for r in rows] == [
        "44730", "44731"
    ]
    assert writes.remembered == [(CHAT, [MINE])]
    assert writes.shared == [], "an ordinary customer is not marked"


# --- the third door: the window sweep ---------------------------------------

def test_the_window_sweep_can_no_longer_reach_a_shared_number(tmp_path, monkeypatch):
    """Against a real database, because this is the half that outlives the
    request and no fake can show it.

    `route()` matches window orders by buyer card first and **by number**
    second, for the case a card cannot cover. For a shared number that second
    rule was the leak the two scenario guards did not close: they refuse once,
    and the sweep runs every two minutes forever. What closes it is the mark —
    after it, `phones()` stops offering the chat and the number rule has nothing
    to match against.
    """
    from core.repos import base as repos_base
    from core.repos.schema import init_db
    from core.repos.users import SqliteCustomerDirectory, save_user
    from core.usecases.sync_incremental import route

    monkeypatch.setattr(repos_base, "DB_PATH", str(tmp_path / "bot_data.db"))
    strangers = [_order(44730, MINE), _order(44731, SOMEBODY_ELSE)]

    async def scenario():
        await init_db()
        d = SqliteCustomerDirectory()
        await save_user(CHAT, NUMBER)
        before = route(strangers, await d.phones(), await d.buyers())
        await d.mark_shared(CHAT)
        after = route(strangers, await d.phones(), await d.buyers())
        # A later lookup that stamps the chat must not undo the refusal.
        await d.mark_asked(CHAT)
        still = route(strangers, await d.phones(), await d.buyers())
        return before, after, still

    before, after, still = asyncio.run(scenario())
    assert list(before) == [CHAT], "this is what the number rule used to do"
    assert after == {}, "and what §4.8 says it must never do"
    assert still == {}, "the mark is not cleared by asking again"


def test_an_ordinary_customer_is_still_matched_by_number(tmp_path, monkeypatch):
    """The number rule exists for a real case — a new buyer card made for a
    number we know — and closing the third door must not close that."""
    from core.repos import base as repos_base
    from core.repos.schema import init_db
    from core.repos.users import SqliteCustomerDirectory, save_user
    from core.usecases.sync_incremental import route

    monkeypatch.setattr(repos_base, "DB_PATH", str(tmp_path / "bot_data.db"))

    async def scenario():
        await init_db()
        d = SqliteCustomerDirectory()
        await save_user(CHAT, NUMBER)
        return route([_order(44730, MINE)], await d.phones(), await d.buyers())

    assert list(asyncio.run(scenario())) == [CHAT]
