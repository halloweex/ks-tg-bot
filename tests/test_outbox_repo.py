"""Capture rules for the queue: who gets handed out, when, and how often.

Most of these are about the moment §6.1 admits it cannot close — the process
dying between `sendMessage` returning and `sent_at` being written. Everything
around that moment is decided here: what an expired lock means, which policy
gets to guess, and why the attempt counter grows when a message is taken rather
than when it fails.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from core.domain.campaign import CampaignKey
from core.repos import base as repos_base
from core.repos import outbox
from core.repos.outbox import (LOCK_FOR, REVIEW, claim, enqueue, mark_failed,
                               mark_sent, park, prune, queue_depth, uncertain)
from core.repos.schema import init_db

CHAT = 555
CAMPAIGN = CampaignKey("stock", "260819")
NOW = datetime(2026, 8, 19, 12, 0, tzinfo=timezone.utc)


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(repos_base, "DB_PATH", str(tmp_path / "bot_data.db"))
    asyncio.run(init_db())


def _put(**kw) -> int:
    """Queued as due at NOW.

    Explicit, because the row is otherwise stamped `datetime('now')` by the
    database while every claim below passes an invented NOW — which made these
    tests pass in the morning and fail after midday UTC. A test that depends on
    the hour it runs at is a test that will be believed on the wrong day.
    """
    payload = kw.pop("payload", {"text": "your cream is back"})
    kw.setdefault("not_before", NOW)
    return asyncio.run(enqueue(CHAT, kw.pop("kind", "stock"), CAMPAIGN, payload, **kw))


def _claim(now: datetime = NOW, limit: int = 50) -> list[dict]:
    return asyncio.run(claim(limit, now=now))


# --- queueing ---------------------------------------------------------------

def test_a_queued_message_comes_back_with_its_campaign(db):
    _put()
    [row] = _claim()
    assert row["chat_id"] == CHAT
    assert row["campaign_key"] == "stock.260819"
    assert row["type"] == "stock"


def test_the_same_dedup_key_queues_once(db):
    """What makes a job safe to run twice. It says nothing about sending twice —
    §6.1 is explicit that nothing does."""
    assert _put(dedup_key="stock:260819:KS-1") is not None
    assert _put(dedup_key="stock:260819:KS-1") is None
    assert len(_claim()) == 1


def test_messages_without_a_dedup_key_do_not_collide(db):
    """Most rows have none, and a unique index over NULLs must not make them one."""
    _put()
    _put()
    assert len(_claim()) == 2


def test_an_unknown_policy_is_refused_at_the_door(db):
    with pytest.raises(ValueError):
        _put(on_uncertain="whatever")


# --- capture ----------------------------------------------------------------

def test_capture_locks_and_counts_the_attempt(db):
    _put()
    [row] = _claim()
    assert row["attempts"] == 1
    assert row["locked_until"] is not None


def test_a_locked_message_is_not_handed_out_again(db):
    """The lock is the whole reason the transaction can be short: capture and
    commit, then send outside it."""
    _put()
    _claim()
    assert _claim(NOW + timedelta(seconds=30)) == []


def test_a_message_not_due_yet_waits(db):
    _put(not_before=NOW + timedelta(minutes=10))
    assert _claim() == []
    assert len(_claim(NOW + timedelta(minutes=11))) == 1


def test_the_oldest_due_message_goes_first(db):
    _put(payload={"text": "second"}, not_before=NOW - timedelta(minutes=1))
    _put(payload={"text": "first"}, not_before=NOW - timedelta(minutes=5))
    assert [r["payload"] for r in _claim()] == ['{"text": "first"}', '{"text": "second"}']


def test_a_sent_message_is_never_handed_out_again(db):
    message_id = _put()
    _claim()
    asyncio.run(mark_sent(message_id))
    assert _claim(NOW + LOCK_FOR + timedelta(minutes=1)) == []


# --- the moment nobody knows ------------------------------------------------

def test_a_crash_leaves_the_lock_expired_and_retry_takes_it_again(db):
    """Nobody clears a lock when the process dies, so an expired one is exactly
    the case where it is unknown whether the message went out. A delivery status
    would rather arrive twice than not at all."""
    _put(kind="delivery")
    _claim()
    [again] = _claim(NOW + LOCK_FOR + timedelta(seconds=1))
    assert again["attempts"] == 2, "the second capture counts, so it cannot spin forever"


def test_the_same_crash_stops_a_broadcast_instead(db):
    """§6.2 the other way round: a duplicate broadcast is worse than a missed
    one, so nothing sends this — it waits for a person."""
    _put(kind="broadcast", on_uncertain=REVIEW)
    _claim()
    later = NOW + LOCK_FOR + timedelta(seconds=1)
    assert _claim(later) == []
    assert [row["type"] for row in asyncio.run(uncertain(now=later))] == ["broadcast"]


def test_a_failure_the_sender_knows_about_is_not_the_uncertain_case(db):
    """Releasing the lock is what says "we know it did not go out". A row let go
    that way is claimable again whatever its policy, because there is nothing
    left to be unsure about."""
    message_id = _put(kind="broadcast", on_uncertain=REVIEW)
    _claim()
    asyncio.run(mark_failed(message_id, "Bad Gateway", retry_in=timedelta(0), now=NOW))

    assert asyncio.run(uncertain(now=NOW + timedelta(minutes=1))) == []
    assert len(_claim(NOW + timedelta(minutes=1))) == 1


def test_a_retry_waits_out_its_backoff(db):
    message_id = _put()
    _claim()
    asyncio.run(mark_failed(message_id, "429", retry_in=timedelta(minutes=30), now=NOW))
    assert _claim(NOW + timedelta(minutes=5)) == []


# --- the shelf --------------------------------------------------------------

def test_a_parked_message_leaves_the_queue_and_stays_on_the_record(db):
    """Deleting would be tidier and would erase what somebody reads when a
    customer says they never got it."""
    message_id = _put()
    asyncio.run(park(message_id, "5 attempts, last: chat not found"))

    assert _claim(NOW + timedelta(hours=1)) == []
    assert asyncio.run(queue_depth(now=NOW))["parked"] == 1


def test_the_depth_counts_what_an_alert_would_report(db):
    sent_id = _put()
    _put()
    _put(not_before=NOW + timedelta(hours=1))
    _claim()
    asyncio.run(mark_sent(sent_id))

    depth = asyncio.run(queue_depth(now=NOW))
    assert depth["sent"] == 1
    assert depth["due"] == 1, "the future one is not due, the sent one is gone"


# --- pruning ----------------------------------------------------------------

def test_the_payload_goes_after_a_week_and_the_header_stays(db):
    """§6.4: a weekly sweep of the whole row would delete the record of a send
    before the purchase it is measured against — the median gap to a repeat
    purchase is 52 days."""
    message_id = _put()
    _claim()
    asyncio.run(mark_sent(message_id))
    _age(message_id, sent_days=10, created_days=10)

    assert asyncio.run(prune())["payloads_cleared"] == 1
    row = _row(message_id)
    assert row["payload"] == "{}"
    assert row["campaign_key"] == "stock.260819" and row["sent_at"] is not None


def test_a_fresh_payload_is_left_alone(db):
    message_id = _put()
    _claim()
    asyncio.run(mark_sent(message_id))
    assert asyncio.run(prune())["payloads_cleared"] == 0


def test_the_header_goes_after_a_year(db):
    message_id = _put()
    _age(message_id, sent_days=400, created_days=400)
    assert asyncio.run(prune())["rows_dropped"] == 1
    assert _row(message_id) is None


# --- the constraint no test can exercise ------------------------------------

def test_the_module_says_it_needs_a_single_sender(db):
    """SQLite has no SKIP LOCKED, so the capture is only safe while exactly one
    process claims — and no in-process test can demonstrate two of them racing
    between the SELECT and the UPDATE. Pinned the way the VerifiedPhone holes
    are: if the sentence goes, this test goes with it, deliberately."""
    assert "one sender" in outbox.__doc__.lower()
    assert "skip locked" in outbox.__doc__.lower()


def _age(message_id: int, *, sent_days: int, created_days: int) -> None:
    """Backdate a row, because the timestamps are written by the database."""
    import aiosqlite

    async def go() -> None:
        async with aiosqlite.connect(repos_base.DB_PATH) as db_:
            await db_.execute(
                "UPDATE outbox SET sent_at = datetime('now', ?), "
                "created_at = datetime('now', ?) WHERE id = ?",
                (f"-{sent_days} days", f"-{created_days} days", message_id),
            )
            await db_.commit()

    asyncio.run(go())


def _row(message_id: int) -> dict | None:
    import aiosqlite

    async def go() -> dict | None:
        async with aiosqlite.connect(repos_base.DB_PATH) as db_:
            db_.row_factory = aiosqlite.Row
            cursor = await db_.execute("SELECT * FROM outbox WHERE id = ?", (message_id,))
            row = await cursor.fetchone()
            return dict(row) if row else None

    return asyncio.run(go())
