"""The /stats readout, from the command an admin types down to the table.

The arithmetic behind it has had tests since it left this handler
(tests/test_analytics_report.py). The handler itself had none, and the two are
different nets. What the move changed here was a call: `usage_report()` grew a
store argument on the day the port arrived, and a call naming the wrong
arguments imports cleanly and raises the moment somebody types the command.
That is §13 of docs/found-during-move.md in the one form neither
test_call_arity.py nor test_port_conformance.py can see — the call is made
inside a handler nothing else imports, against a store the handler builds
itself.

So nothing here is patched. The handler constructs its own SqliteUsageStats,
that store reads a real temporary database, and every assertion is about the
text that came back — which is the only thing an admin ever sees.
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from bot.handlers.broadcast import cmd_stats
from core.config import AppConfig
from core.repos import base as repos_base
from core.repos.events import log_event
from core.repos.schema import init_db
from core.usecases.analytics import FUNNEL_STEPS

ADMIN = 4242
STRANGER = 99
CUSTOMER = 500


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(repos_base, "DB_PATH", str(tmp_path / "bot_data.db"))
    asyncio.run(init_db())


def _config() -> AppConfig:
    return SimpleNamespace(env=SimpleNamespace(admin_ids=[ADMIN]))


class _Message:
    """As much of a Message as the handler touches."""

    def __init__(self, user_id: int = ADMIN) -> None:
        self.from_user = SimpleNamespace(id=user_id)
        self.replies: list[str] = []
        self.kwargs: dict = {}

    async def answer(self, text: str, **kwargs) -> None:
        self.replies.append(text)
        self.kwargs = kwargs


def _readout(user_id: int = ADMIN) -> _Message:
    return _run(_Message(user_id))


def _run(message: _Message) -> _Message:
    asyncio.run(cmd_stats(message, _config()))
    return message


def _text() -> str:
    [reply] = _readout().replies
    return reply


def _section(text: str, heading: str) -> list[str]:
    """The indented lines under one bold heading of the readout."""
    lines = text.split("\n")
    start = next(i for i, line in enumerate(lines) if heading in line)
    out = []
    for line in lines[start + 1:]:
        if not line.startswith("  "):
            break
        out.append(line.strip())
    return out


def _event(chat_id: int, event: str, **meta) -> None:
    asyncio.run(log_event(chat_id, event, json.dumps(meta)))


def _viewed(chat_id: int, *, found: int, cached: bool = False) -> None:
    """One order lookup, in the shape bot/handlers/orders.py records it."""
    _event(chat_id, "orders_viewed", found=found, cached=cached)


# --- who may ask ------------------------------------------------------------

def test_a_stranger_asking_for_stats_is_answered_with_nothing(db):
    """Not an error message: the bot does not confirm that the command exists."""
    assert _readout(STRANGER).replies == []


# --- the call the move changed ----------------------------------------------

def test_an_admin_gets_a_readout_even_from_an_empty_database(db):
    """The test this file exists for.

    Everything below is about wording; this one is only about the command
    answering at all. A forgotten argument to usage_report takes exactly that
    away, and takes it away silently — at import there is nothing wrong, and
    the first person to find out is the admin who typed /stats.
    """
    message = _readout()
    assert len(message.replies) == 1
    assert message.kwargs["parse_mode"] == "HTML"


def test_the_readout_has_all_five_sections(db):
    text = _text()
    for heading in ("Funnel (unique users)", "Order lookups", "Retention",
                    "Events, last", "Proactive, last"):
        assert heading in text, heading


# --- the numbers are the table's ---------------------------------------------

def test_the_funnel_counts_people_and_not_events(db):
    """Two taps by one person are one person: the column is distinct chat_id,
    and a funnel that counted rows would flatter every step."""
    _event(1, "start")
    _event(1, "start")
    _event(2, "start")
    _event(1, "registered")

    funnel = _section(_text(), "Funnel (unique users)")
    assert funnel[0] == "/start: 2  100%"
    assert "registered: 1  50%" in funnel


def test_every_step_of_the_funnel_has_a_word_for_it(db):
    """The scenario decides which steps make a funnel; this handler decides what
    they are called, and nothing links the two lists.

    A step added there and not here prints its own key — `first_order: 3` — into
    an admin's chat, which reads like a name leaking out of the database rather
    than like a label nobody wrote. Spelled out here on purpose: this list is
    the second place, so it is the one that has to be edited deliberately.
    """
    captions = [line.split(":")[0]
                for line in _section(_text(), "Funnel (unique users)")]
    assert captions == ["/start", "shared contact", "registered", "viewed orders"]
    assert len(captions) == len(FUNNEL_STEPS)


def test_a_miss_is_counted_only_against_lookups_that_asked_the_crm(db):
    """A list served from cache says nothing about whether the phone matched."""
    _viewed(CUSTOMER, found=0)
    _viewed(CUSTOMER, found=3)
    _viewed(CUSTOMER, found=0, cached=True)

    assert "found nothing: 1 of 2 (50%)" in _section(_text(), "Order lookups")


def test_retention_prints_the_active_first_and_the_returning_second(db):
    """Both numbers are ints about people, so a transposed pair reads perfectly
    well and claims that everybody came back on a second day."""
    _event(1, "start")
    _event(2, "start")

    assert _section(_text(), "Retention") == [
        "active: 2, of them on more than one day: 0"
    ]


def test_the_events_section_counts_occurrences_and_people_apart(db):
    """One screen opened three times by two people is not three people."""
    _event(1, "menu_opened")
    _event(1, "menu_opened")
    _event(2, "menu_opened")

    assert "menu_opened: 3 (2 users)" in _section(_text(), "Events, last")


# --- an empty database ------------------------------------------------------

def test_nothing_measured_yet_is_said_in_words_rather_than_as_a_zero(db):
    """"Nobody has looked yet" and "the phone match works perfectly" are
    opposite facts, and 0% is how the first turns into the second."""
    text = _text()
    assert _section(text, "Order lookups") == ["no lookups yet"]
    assert _section(text, "Events, last") == ["no events yet"]
    assert "%" not in text, "an empty funnel has no shares, not shares of 0%"


def test_the_headings_carry_the_windows_the_report_actually_used(db):
    """Printed from the report rather than as literals here, so the caption and
    the query it describes cannot drift apart."""
    text = _text()
    assert "Last 30 days" in text
    assert "Events, last 7 days" in text


# --- what the bot sent, and what happened next -------------------------------
#
# The arithmetic has its own file (tests/test_campaign_report.py). These are
# about the readout: a section added to a handler and never rendered is a
# section that raises the first time an admin types the command, and nothing
# before this exercised it.


def _sent(chat_id: int, key: str, *, type_: str = "stock",
          when: str = "-1 days", failed: bool = False) -> None:
    async def go() -> None:
        async with repos_base.connect() as db_:
            await db_.execute(
                "INSERT INTO outbox (chat_id, type, campaign_key, payload, "
                "                    sent_at, failed_at, created_at) "
                "VALUES (?, ?, ?, '{}', "
                "        CASE WHEN ? THEN NULL ELSE datetime('now', ?) END, "
                "        CASE WHEN ? THEN datetime('now', ?) ELSE NULL END, "
                "        datetime('now', ?))",
                (chat_id, type_, key, failed, when, failed, when, when))
            await db_.commit()
    asyncio.run(go())


def _bought(chat_id: int, when: str) -> None:
    """An order, spelled the way KeyCRM spells it — a T and a trailing Z."""
    async def go() -> None:
        async with repos_base.connect() as db_:
            stamp = await (await db_.execute(
                "SELECT strftime('%Y-%m-%dT%H:%M:%S.000000Z', "
                "                datetime('now', ?))", (when,))).fetchone()
            await db_.execute(
                "INSERT INTO orders (chat_id, source, source_order_id, "
                "                    ordered_at, products_json) "
                "VALUES (?, 'keycrm', ?, ?, '[]')",
                (chat_id, f"{chat_id}{when}", stamp[0]))
            await db_.commit()
    asyncio.run(go())


def test_with_nothing_sent_the_section_says_so(db):
    assert _section(_text(), "Proactive, last") == ["nothing sent yet"]


def test_a_campaign_shows_who_got_it_and_who_ordered_after(db):
    _sent(1, "stock.260910")
    _sent(2, "stock.260910")
    _bought(1, "-12 hours")

    [caption, line] = _section(_text(), "Proactive, last")
    assert "ordered within" in caption
    assert line.startswith("stock.260910 (stock):")
    assert "1/2" in line and "after" in line


def test_an_undelivered_message_is_named_rather_than_hidden(db):
    """Somebody who blocked the bot is the cost of the channel, and a readout
    that prints only what worked is a readout that flatters it."""
    _sent(1, "stock.260910")
    _sent(2, "stock.260910", failed=True)

    [_caption, line] = _section(_text(), "Proactive, last")
    assert "1 undelivered" in line


def test_a_campaign_a_purchase_caused_shows_a_dash_and_not_a_zero(db):
    """Rivo awards points FOR an order, so the triggering order sits in the
    before-window by construction. A zero there would read as "she would have
    bought anyway" about the very purchase that sent the message; a dash says
    the comparison is meaningless, which is the truth."""
    _sent(3, "loyalty.260910", type_="loyalty")
    _bought(3, "-36 hours")

    [_caption, line] = _section(_text(), "Proactive, last")
    assert "— before" in line, line


def test_the_admin_s_own_completion_report_never_appears(db):
    """It is queued under the same bcast.<job_id> key as the broadcast itself,
    so without excluding the type every broadcast gains a recipient who never
    buys."""
    _sent(1, "bcast.77", type_="broadcast")
    _sent(ADMIN, "bcast.77", type_="broadcast_report")

    lines = _section(_text(), "Proactive, last")
    assert not any("broadcast_report" in line for line in lines), lines


def test_the_readout_says_plainly_that_a_tap_cannot_be_counted(db):
    """The caveat belongs in the readout, not in a document nobody opens with
    the numbers in front of them."""
    text = _text()
    assert "no click column" in text
    assert "unmeasurable, never zero" in text
