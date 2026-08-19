"""What happens to one message, for each way a send can end.

The sender has a fake transport and a real database: every outcome below is
read back out of the queue, because the queue is what survives a restart and
the counters are not.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from core.domain.campaign import CampaignKey
from core.ports.notifier import RateLimited, RecipientGone
from core.repos import base as repos_base
from core.repos import outbox
from core.repos.outbox import LOCK_FOR, MAX_ATTEMPTS, REVIEW, claim, enqueue
from core.repos.schema import init_db
from core.repos.users import is_opted_out, save_user
from core.usecases.notify import backoff, deliver_once

CHAT = 555
CAMPAIGN = CampaignKey("stock", "260819")
# Midday in Kyiv, so quiet hours are off unless a test asks for them.
NOON = datetime(2026, 8, 19, 9, 0, tzinfo=timezone.utc)
NIGHT = datetime(2026, 8, 19, 0, 30, tzinfo=timezone.utc)


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(repos_base, "DB_PATH", str(tmp_path / "bot_data.db"))
    asyncio.run(init_db())


class FakeNotifier:
    """Records what it was asked to send, or raises what it was told to."""

    def __init__(self, raises: Exception | None = None) -> None:
        self.sent: list[tuple[int, dict, bool]] = []
        self.raises = raises

    async def send(self, chat_id: int, payload: dict, *, silent: bool) -> None:
        self.sent.append((chat_id, payload, silent))
        if self.raises is not None:
            raise self.raises


def _put(**kw) -> int:
    """Queued as due at NOON, because the row is otherwise stamped by the
    database clock and the pass reads the one the test injects."""
    kw.setdefault("not_before", NOON)
    return asyncio.run(enqueue(CHAT, kw.pop("kind", "stock"), CAMPAIGN,
                               kw.pop("payload", {"text": "back in stock"}), **kw))


def _row(message_id: int) -> dict:
    import aiosqlite

    async def go() -> dict:
        async with aiosqlite.connect(repos_base.DB_PATH) as db_:
            db_.row_factory = aiosqlite.Row
            cursor = await db_.execute("SELECT * FROM outbox WHERE id = ?", (message_id,))
            return dict(await cursor.fetchone())

    return asyncio.run(go())


# --- the ordinary case ------------------------------------------------------

def test_a_due_message_is_sent_and_marked(db):
    message_id = _put()
    result = asyncio.run(deliver_once(FakeNotifier(), now=NOON))

    assert result.sent == 1
    assert _row(message_id)["sent_at"] is not None


def test_the_campaign_key_travels_with_the_payload(db):
    """§6.4: the button on the message has to carry it, so the transport has to
    be handed it — the row knows it, the payload did not."""
    _put(payload={"text": "back in stock"})
    notifier = FakeNotifier()
    asyncio.run(deliver_once(notifier, now=NOON))

    assert notifier.sent[0][1]["campaign_key"] == "stock.260819"


def test_nothing_due_is_a_quiet_pass(db):
    result = asyncio.run(deliver_once(FakeNotifier(), now=NOON))
    assert result == type(result)()


def test_at_night_the_message_still_goes_but_silently(db):
    """Not deferred until morning: a restock at 03:00 is still true at 03:00.
    It arrives without a sound and is waiting, unread and unresented."""
    _put(not_before=NIGHT)
    notifier = FakeNotifier()
    asyncio.run(deliver_once(notifier, now=NIGHT))

    assert notifier.sent[0][2] is True
    notifier_day = FakeNotifier()
    _put()
    asyncio.run(deliver_once(notifier_day, now=NOON))
    assert notifier_day.sent[0][2] is False


# --- the ways it fails ------------------------------------------------------

def test_a_rate_limit_reschedules_by_the_wait_the_transport_named(db):
    """Guessing shorter earns another 429; guessing longer wastes the time it
    just told us about."""
    message_id = _put()
    asyncio.run(deliver_once(FakeNotifier(RateLimited(45)), now=NOON))

    row = _row(message_id)
    assert row["sent_at"] is None
    assert row["not_before"] == "2026-08-19 09:00:45"
    assert row["locked_until"] is None, "released: we know it did not go out"


def test_a_blocked_chat_is_parked_and_unsubscribed(db):
    """Both halves. The message can never arrive, and neither can the next one —
    without the unsubscribe a blocked chat costs a send slot on every broadcast
    from here on."""
    asyncio.run(save_user(CHAT, "+380670000000"))
    message_id = _put()

    result = asyncio.run(deliver_once(FakeNotifier(RecipientGone("blocked")), now=NOON))

    assert (result.parked, result.unsubscribed) == (1, 1)
    assert _row(message_id)["failed_at"] is not None
    assert asyncio.run(is_opted_out(CHAT)) is True


def test_an_unknown_failure_is_retried_with_a_backoff(db):
    message_id = _put()
    asyncio.run(deliver_once(FakeNotifier(RuntimeError("bad gateway")), now=NOON))

    row = _row(message_id)
    assert row["last_error"].startswith("RuntimeError")
    assert row["not_before"] == "2026-08-19 09:01:00", "one minute after the first try"


def test_the_backoff_doubles_and_then_stops(db):
    assert backoff(1) == timedelta(minutes=1)
    assert backoff(3) == timedelta(minutes=4)
    assert backoff(99) == timedelta(minutes=30), "past half an hour a person should look"


def test_a_message_that_will_never_send_lands_on_the_shelf(db):
    """attempts is counted at capture, so a message that kills the process still
    reaches the limit — which is the whole reason it is counted there."""
    message_id = _put()
    notifier = FakeNotifier(RuntimeError("still broken"))

    moment = NOON
    for _ in range(MAX_ATTEMPTS + 1):
        asyncio.run(deliver_once(notifier, now=moment))
        moment += timedelta(hours=1)

    row = _row(message_id)
    assert row["failed_at"] is not None
    assert row["attempts"] == MAX_ATTEMPTS + 1
    assert len(notifier.sent) == MAX_ATTEMPTS, "the last capture parked instead of sending"


# --- the case nobody can resolve --------------------------------------------

def test_a_broadcast_left_in_doubt_is_parked_rather_than_resent(db):
    """§6.2. The row was captured, the process died, and nobody can say whether
    it went out. Sending again is the failure this policy exists to prevent."""
    message_id = _put(kind="broadcast", on_uncertain=REVIEW)
    asyncio.run(claim(now=NOON))          # captured, then the process "dies"

    notifier = FakeNotifier()
    result = asyncio.run(deliver_once(notifier, now=NOON + LOCK_FOR + timedelta(minutes=1)))

    assert notifier.sent == []
    assert result.parked == 1
    assert "review" in _row(message_id)["last_error"]


def test_a_delivery_status_left_in_doubt_is_sent_again(db):
    """The same crash, the opposite policy: a missed parcel notification is
    worse than one that arrives twice."""
    _put(kind="delivery")
    asyncio.run(claim(now=NOON))

    notifier = FakeNotifier()
    result = asyncio.run(deliver_once(notifier, now=NOON + LOCK_FOR + timedelta(minutes=1)))

    assert result.sent == 1
    assert len(notifier.sent) == 1


# --- one bad message must not stop the batch --------------------------------

def test_one_failing_message_does_not_hold_up_the_others(db):
    class OnlyChatOneFails:
        def __init__(self) -> None:
            self.sent: list[int] = []

        async def send(self, chat_id: int, payload: dict, *, silent: bool) -> None:
            if chat_id == CHAT:
                raise RuntimeError("this one is cursed")
            self.sent.append(chat_id)

    _put()
    asyncio.run(enqueue(556, "stock", CAMPAIGN, {"text": "hi"}, not_before=NOON))
    asyncio.run(enqueue(557, "stock", CAMPAIGN, {"text": "hi"}, not_before=NOON))

    notifier = OnlyChatOneFails()
    result = asyncio.run(deliver_once(notifier, now=NOON))

    assert notifier.sent == [556, 557]
    assert (result.sent, result.retried) == (2, 1)


def test_a_pruned_payload_does_not_take_the_pass_down(db):
    """§6.4 blanks the payload after a week; a row somehow claimed afterwards
    must fail as one message, not as a decode error in the middle of a batch."""
    message_id = _put(payload={"text": "hi"})
    import aiosqlite

    async def blank() -> None:
        async with aiosqlite.connect(repos_base.DB_PATH) as db_:
            await db_.execute("UPDATE outbox SET payload = 'not json' WHERE id = ?",
                              (message_id,))
            await db_.commit()

    asyncio.run(blank())
    notifier = FakeNotifier()
    result = asyncio.run(deliver_once(notifier, now=NOON))

    assert result.sent == 1
    assert notifier.sent[0][1] == {"campaign_key": "stock.260819"}


def test_the_pass_reports_what_it_did(db):
    _put()
    assert asyncio.run(deliver_once(FakeNotifier(), now=NOON)).touched == 1
    assert outbox.MAX_ATTEMPTS == 5
