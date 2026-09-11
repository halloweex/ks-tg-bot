"""What the proactive messages did, read out of what was already on disk.

§6.4 asked for sent → clicked → bought and this bot can only see two of those
three. The tests here are mostly about the seams where such a report lies:
a purchase counted for the wrong campaign, one counted that happened before the
message, an admin counted as a recipient, and a percentage printed over a
sample of two.
"""
from __future__ import annotations

import asyncio

import pytest

from core.repos import base as repos_base
from core.repos.campaigns import SqliteCampaignOutcomes, campaign_outcomes
from core.repos.schema import init_db
from core.usecases.analytics import campaign_report

CHAT, OTHER = 501, 502


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(repos_base, "DB_PATH", str(tmp_path / "bot_data.db"))
    asyncio.run(init_db())


def _sent(chat_id: int, key: str, *, at: str, type_: str = "stock",
          failed: bool = False) -> None:
    """One row in the outbox, already delivered — which is the only kind this
    report looks at."""
    async def go() -> None:
        async with repos_base.connect() as conn:
            await conn.execute(
                "INSERT INTO outbox (chat_id, type, campaign_key, payload, "
                "                    sent_at, failed_at, created_at) "
                "VALUES (?, ?, ?, '{}', ?, ?, ?)",
                (chat_id, type_, key, None if failed else at,
                 at if failed else None, at))
            await conn.commit()
    asyncio.run(go())


def _ordered(chat_id: int, at: str) -> None:
    """One cached order, spelled the way production spells it.

    Read off the live database rather than invented: KeyCRM writes
    "2026-07-18T10:27:32.000000Z" — a T, microseconds and a trailing Z — while
    `outbox.sent_at` is "2026-08-20 07:19:48". Both are UTC and SQLite's
    `datetime()` normalises both, which is the only reason the comparison in the
    query is sound. The first draft of these tests used "2026-09-11T14:00:00",
    a spelling this shop's data does not contain, and would have proved nothing
    about the format that does."""
    async def go() -> None:
        async with repos_base.connect() as conn:
            await conn.execute(
                "INSERT INTO orders (chat_id, source, source_order_id, "
                "                    ordered_at, products_json) "
                "VALUES (?, 'keycrm', ?, ?, '[]')",
                (chat_id, f"{chat_id}-{at}", at))
            await conn.commit()
    asyncio.run(go())


def _outcomes(**kw):
    kw.setdefault("days", 30)
    kw.setdefault("window_days", 7)
    return asyncio.run(campaign_outcomes(**kw))


def _report(**kw):
    return asyncio.run(campaign_report(SqliteCampaignOutcomes(), **kw))


# --- the half of the funnel that is real -------------------------------------

def test_somebody_who_ordered_after_the_message_is_counted(db):
    _sent(CHAT, "stock.260910", at="2026-09-10 09:00:00")
    _ordered(CHAT, "2026-09-11T14:00:00.000000Z")

    (row,) = _outcomes()
    assert row[0] == "stock.260910"
    assert row[2] == 1, "one person got it"
    assert row[5] == 1, "and she ordered inside the window"


def test_an_order_outside_the_window_is_not_the_campaign_s(db):
    _sent(CHAT, "stock.260910", at="2026-09-10 09:00:00")
    _ordered(CHAT, "2026-09-25T14:00:00.000000Z")

    (row,) = _outcomes(window_days=7)
    assert row[5] == 0, "a fortnight later is not 'after the message'"


def test_somebody_else_s_order_is_not_counted(db):
    _sent(CHAT, "stock.260910", at="2026-09-10 09:00:00")
    _ordered(OTHER, "2026-09-11T14:00:00.000000Z")

    (row,) = _outcomes()
    assert row[5] == 0


# --- the character that would have made it lie -------------------------------

def test_an_order_an_hour_before_the_message_is_not_counted_as_after(db):
    """`sent_at` is written by datetime('now') — "2026-09-10 09:00:00". The
    CRM's `ordered_at` is ISO with a T. Compared as text, position ten is 'T'
    against ' ', and 'T' sorts higher: an order placed an HOUR BEFORE the
    message reads as one placed after it, and the campaign takes credit for a
    purchase that preceded it.

    Measured, not assumed: SQLite answers 1 to the raw comparison where the
    truth is 0. Both sides go through datetime() for this one test's sake, and
    it is the only reason the query is not two lines shorter."""
    _sent(CHAT, "stock.260910", at="2026-09-10 09:00:00")
    _ordered(CHAT, "2026-09-10T08:00:00.000000Z")

    (row,) = _outcomes()
    assert row[5] == 0, (
        "an order made before the message was counted as made after it")
    assert row[6] == 1, "it belongs in the 'before' window instead"


def test_an_order_with_no_date_is_neither_before_nor_after(db):
    """`orders.ordered_at` defaults to the empty string, and datetime('') is
    NULL — which fails every comparison rather than passing the wrong one."""
    _sent(CHAT, "stock.260910", at="2026-09-10 09:00:00")
    _ordered(CHAT, "")

    (row,) = _outcomes()
    assert (row[5], row[6]) == (0, 0)


# --- the admin who is not a customer -----------------------------------------

def test_the_admin_s_completion_report_is_not_a_recipient(db):
    """core/usecases/broadcast.py queues the admin's summary under the SAME
    bcast.<job_id> key as the broadcast. Grouping on the key alone hands every
    broadcast one extra 'recipient' who never buys and quietly lowers the
    share."""
    for chat in (CHAT, OTHER):
        _sent(chat, "bcast.77", at="2026-09-10 09:00:00", type_="broadcast")
    _sent(999, "bcast.77", at="2026-09-10 09:05:00", type_="broadcast_report")

    rows = _outcomes()
    assert len(rows) == 1, f"the admin's report leaked into the campaign: {rows}"
    assert rows[0][2] == 2, "two customers, not three"


def test_a_support_reply_is_not_a_campaign(db):
    """A manager's answer, inside a conversation that is very often about an
    order. Counting purchases after it would credit the campaign machinery with
    a person's work and make support the best converting thing the shop does."""
    _sent(CHAT, "support.260910", at="2026-09-10 09:00:00", type_="support")
    _ordered(CHAT, "2026-09-10T18:00:00.000000Z")

    assert _outcomes() == []


# --- what the report refuses to say ------------------------------------------

def test_a_percentage_is_not_printed_over_a_sample_of_two(db):
    """Birthdays reach one to three people on an ordinary day. "1 of 2 ordered"
    is a fact about one woman; "50%" is a claim about a channel."""
    for chat in (CHAT, OTHER):
        _sent(chat, "bday.260910", at="2026-09-10 09:00:00", type_="bday")
    _ordered(CHAT, "2026-09-11T10:00:00.000000Z")

    (row,) = _report().rows
    assert row.bought == 1 and row.people == 2
    assert row.share is None, "a share over two people is arithmetic, not a finding"


def test_a_campaign_caused_by_a_purchase_has_no_before_window(db):
    """Rivo awards points FOR an order, so the order that triggered the message
    sits in the 'before' window by construction. Left in, the row reads as "she
    would have bought anyway" about the very purchase that caused the send."""
    _sent(CHAT, "loyalty.260910", at="2026-09-10 09:00:00", type_="loyalty")
    _ordered(CHAT, "2026-09-09T18:00:00.000000Z")

    (row,) = _report().rows
    assert row.bought_before is None, (
        "the triggering purchase was offered as a background rate")
    assert row.share_before is None


def test_an_ordinary_campaign_keeps_its_before_window(db):
    """The discipline above must not swallow the comparison everywhere — it is
    what turns "5 of 38 ordered" into something an owner can act on."""
    _sent(CHAT, "stock.260910", at="2026-09-10 09:00:00")
    _ordered(CHAT, "2026-09-09T18:00:00.000000Z")

    (row,) = _report().rows
    assert row.bought_before == 1


def test_a_key_that_is_not_a_key_costs_one_row_and_not_the_report(db):
    """`parse` raises so a malformed key cannot be counted as an unnamed
    campaign. Here that has to mean one bad row, not one bad report — and the
    count has to be reported, because a number climbing there is how a sender
    that stopped spelling its key properly becomes visible."""
    _sent(CHAT, "stock.260910", at="2026-09-10 09:00:00")
    _sent(OTHER, "nonsense", at="2026-09-10 09:00:00")

    report = _report()
    assert [r.key for r in report.rows] == ["stock.260910"]
    assert report.unreadable == 1


def test_a_message_that_never_arrived_is_not_a_send(db):
    """Failed rows keep their place in the journal and their campaign key. They
    are counted as undelivered, and the people they were aimed at are not
    counted as people who got anything."""
    _sent(CHAT, "stock.260910", at="2026-09-10 09:00:00")
    _sent(OTHER, "stock.260910", at="2026-09-10 09:00:00", failed=True)

    (row,) = _outcomes()
    assert row[2] == 1, "only the delivered one is a recipient"
    assert row[4] == 1, (
        "the undelivered one is the cost of the channel and has to be counted; "
        "filtering failed rows out of the query made this column always zero")


def test_two_kinds_of_message_under_one_key_stay_two_rows(db):
    """The grouping is by (key, type), and the test above does not prove it:
    `broadcast_report` is excluded by type before the grouping can matter, so
    grouping on the key alone passes that test unchanged. Verified by mutation.

    This pins the invariant itself. Nothing in the tree collides this way today
    — `daily(KIND)` puts the kind in the key — but `campaign_for(job_id)` shows
    a key can be minted for a job rather than a kind, and the moment two senders
    share one, a report grouped on the key alone merges two audiences into one
    denominator and reports a share of a population that never existed."""
    _sent(CHAT, "bcast.77", at="2026-09-10 09:00:00", type_="broadcast")
    _sent(OTHER, "bcast.77", at="2026-09-10 09:00:00", type_="stock")

    rows = _outcomes()
    assert len(rows) == 2, f"two audiences merged into one row: {rows}"
    assert {r[1] for r in rows} == {"broadcast", "stock"}
    assert all(r[2] == 1 for r in rows), "each keeps its own recipients"
