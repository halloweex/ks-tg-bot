"""The sweep that joins the warehouse's buyers to this bot's chats.

Every case here is about what the bot does with an answer, not about the answer:
the classifier and its confidence live in another repository, and the rules this
scenario adds are the ones that repository cannot know — a chat is several buyer
cards, a chat may be no cards at all, and the other database may be down.
"""
from __future__ import annotations

import asyncio

import pytest

from core.domain.gender import Gender
from core.ports.gender import BuyerGenders
from core.ports.users import GenderForm
from core.usecases.gender import refresh_forms


class FakeDirectory:
    """Only the two reads the sweep uses; the rest of the port is not its
    business and a fake that implemented it would invite a test to use it."""

    def __init__(self, cards: dict[int, set[str]], registered: set[int] = frozenset()):
        self._cards = cards
        self._registered = set(registered) | set(cards)

    async def buyers(self) -> list[tuple[int, str]]:
        return [(chat, card) for chat, cards in self._cards.items() for card in cards]

    async def phones(self) -> list[tuple[int, str]]:
        return [(chat, "+380670000000") for chat in sorted(self._registered)]


class FakeWarehouse:
    def __init__(self, table: dict[str, str | None], fail: Exception | None = None):
        self.table = table
        self.fail = fail
        self.asked: list[set[str]] = []

    async def for_buyers(self, buyer_ids: set[str]) -> dict[str, str | None]:
        self.asked.append(set(buyer_ids))
        if self.fail:
            raise self.fail
        return {b: self.table[b] for b in buyer_ids if b in self.table}


class FakeForms:
    def __init__(self, stored: dict[int, str] | None = None):
        self.stored = dict(stored or {})
        self.writes: list[tuple[int, str]] = []

    async def form_for(self, chat_id: int) -> str | None:
        return self.stored.get(chat_id)

    async def remember(self, chat_id: int, form: str) -> None:
        self.stored[chat_id] = form
        self.writes.append((chat_id, form))


def test_the_fakes_are_the_ports():
    assert isinstance(FakeWarehouse({}), BuyerGenders)
    assert isinstance(FakeForms(), GenderForm)


def _run(directory, warehouse, forms, **kw):
    return asyncio.run(refresh_forms(directory, warehouse, forms, **kw))


def test_one_card_one_answer():
    forms = FakeForms()
    changed = _run(FakeDirectory({7: {"6477"}}), FakeWarehouse({"6477": "m"}), forms)
    assert changed == {7: "m"}
    assert forms.stored == {7: "m"}


def test_cards_that_disagree_leave_the_wording_unmarked():
    """Two buyer cards behind one number are two people. The bot cannot tell
    which one is typing, so it stops marking gender rather than picking one."""
    forms = FakeForms()
    _run(FakeDirectory({7: {"1", "2"}}), FakeWarehouse({"1": "f", "2": "m"}), forms)
    assert forms.stored == {7: Gender.UNKNOWN.value}


def test_a_registered_chat_with_no_card_is_answered_not_skipped():
    """Registered, never ordered — the state two of the three people in
    production are in. Nothing is known about them, and that is the unmarked
    form's case, not the feminine one's."""
    forms = FakeForms()
    _run(FakeDirectory({}, registered={7}), FakeWarehouse({}), forms)
    assert forms.stored == {7: Gender.UNKNOWN.value}


def test_a_buyer_the_table_has_never_heard_of_is_the_same_as_a_null():
    forms = FakeForms()
    _run(FakeDirectory({7: {"6477"}}), FakeWarehouse({}), forms)
    assert forms.stored == {7: Gender.UNKNOWN.value}
    forms = FakeForms()
    _run(FakeDirectory({7: {"6477"}}), FakeWarehouse({"6477": None}), forms)
    assert forms.stored == {7: Gender.UNKNOWN.value}


def test_nothing_is_written_where_nothing_changed():
    """The sweep runs every hour over a base that mostly does not move, and
    `users.updated_at` must go on meaning "this profile changed"."""
    forms = FakeForms({7: "f"})
    changed = _run(FakeDirectory({7: {"6477"}}), FakeWarehouse({"6477": "f"}), forms)
    assert changed == {}
    assert forms.writes == []


def test_a_correction_by_hand_arrives_on_the_next_round():
    """`override_by_human` in the warehouse is the whole reason this is a loop
    and not a one-off backfill."""
    forms = FakeForms({7: "f"})
    changed = _run(FakeDirectory({7: {"6477"}}), FakeWarehouse({"6477": "m"}), forms)
    assert changed == {7: "m"}


def test_the_warehouse_is_asked_once_for_every_card_in_the_base():
    """One query per sweep, not one per chat: the join is ours to make and the
    other database is somebody else's to protect."""
    warehouse = FakeWarehouse({"1": "f", "2": "m", "3": None})
    _run(FakeDirectory({7: {"1"}, 8: {"2", "3"}}), warehouse, FakeForms())
    assert warehouse.asked == [{"1", "2", "3"}]


def test_one_chat_can_be_refreshed_on_its_own():
    """What registration uses: the customer is standing in front of us and the
    hourly sweep is up to an hour away."""
    warehouse = FakeWarehouse({"1": "f", "2": "m"})
    forms = FakeForms()
    changed = _run(FakeDirectory({7: {"1"}, 8: {"2"}}), warehouse, forms, only={8})
    assert changed == {8: "m"}
    assert forms.stored == {8: "m"}
    assert warehouse.asked == [{"2"}], "nobody else's cards are asked about"


def test_the_other_database_being_down_changes_nothing():
    """Not caught here: the caller decides, and both callers decide to leave
    every form exactly as it was."""
    forms = FakeForms({7: "f"})
    with pytest.raises(RuntimeError):
        _run(FakeDirectory({7: {"1"}}), FakeWarehouse({}, fail=RuntimeError("down")),
             forms)
    assert forms.writes == []


def test_an_empty_base_asks_nothing():
    warehouse = FakeWarehouse({})
    assert _run(FakeDirectory({}), warehouse, FakeForms()) == {}
    assert warehouse.asked == []


# --- the loop around it ------------------------------------------------------
def test_one_bad_round_does_not_kill_the_loop(monkeypatch):
    """The other database belongs to another compose project, with its own
    deploys and its own restarts, so it *will* be unreachable sometimes. What
    that may cost is an hour of nobody's form changing — not the sweep."""
    import bot.gender as loop

    monkeypatch.setattr(loop, "POLL_INTERVAL_SECONDS", 0)
    rounds = []

    async def flaky(directory, source, forms):
        rounds.append(len(rounds) + 1)
        if len(rounds) == 1:
            raise RuntimeError("warehouse is down")
        if len(rounds) == 2:
            return {7: "m"}
        raise asyncio.CancelledError  # stop the loop from the inside

    monkeypatch.setattr(loop, "refresh_forms", flaky)

    async def scenario():
        with pytest.raises(asyncio.CancelledError):
            await loop.watch(FakeWarehouse({}), FakeDirectory({}), FakeForms())

    asyncio.run(scenario())
    assert rounds == [1, 2, 3], "the round after the failure must still happen"


def test_cancellation_is_not_swallowed_as_a_bad_round():
    """A shutdown has to end the loop. `except Exception` would not catch
    CancelledError on 3.8+, and the explicit re-raise above it says so out loud
    rather than relying on that."""
    import inspect

    import bot.gender as loop

    body = inspect.getsource(loop.watch)
    assert "except asyncio.CancelledError:" in body
    assert body.index("except asyncio.CancelledError:") < body.index("except Exception")
