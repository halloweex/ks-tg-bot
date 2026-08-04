"""The alert §5.5 asks for, and the line the customer sees when it fires.

"The bot reads only from the database" means a stopped sync is stale orders for
everybody with no fallback to the live API — and the ways it stops are the ways
that raise nothing. A cancelled task, a loop nobody schedules any more, a sweep
hanging on a socket: an `except` never sees any of them, and monitoring built
on catching errors reports a healthy system for as long as it takes somebody to
notice by hand.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from bot.sync import (REALERT_AFTER, SILENCE_AFTER, _alert_text, data_age,
                      silence, watch_for_silence)
from core.i18n import customer_texts
from core.repos import base as repos_base
from core.repos import sync_state
from core.repos.schema import init_db
from core.usecases.sync_incremental import SOURCE

NOW = datetime(2026, 8, 4, 16, 20, 0, tzinfo=timezone.utc)
STARTED = NOW - timedelta(hours=2)
ADMIN = 4242


def test_silence_is_measured_from_the_last_success():
    state = {"last_success_at": "2026-08-04 16:05:00"}
    assert silence(state, now=NOW, since=STARTED) == timedelta(minutes=15)


def test_a_sweep_that_never_succeeded_is_measured_from_startup():
    """The failure this exists for. With no fallback, a sync that never ran once
    would report a silence of zero forever and never alert."""
    assert silence(None, now=NOW, since=STARTED) == timedelta(hours=2)
    assert silence({"last_error": "boom"}, now=NOW, since=STARTED) == timedelta(hours=2)


def test_an_error_does_not_shorten_the_silence():
    """A failing sweep is not a succeeding one. The row records both; only one
    of them means the orders moved."""
    state = {"last_success_at": "2026-08-04 15:00:00", "last_error": "ReadTimeout"}
    assert silence(state, now=NOW, since=STARTED) == timedelta(hours=1, minutes=20)


def test_the_alert_says_what_broke_when_there_is_an_error():
    text = _alert_text(
        {"last_success_at": "2026-08-04 15:00:00", "last_error": "ReadTimeout: page 3"},
        timedelta(minutes=80),
    )
    assert "80 min" in text
    assert "ReadTimeout: page 3" in text


def test_the_alert_says_so_when_nothing_even_tried():
    """No error and no success is the diagnosis, not a gap in it: nothing is
    failing because nothing is being attempted, which points at the loop rather
    than at the CRM."""
    assert "not running at all" in _alert_text(None, timedelta(minutes=20))


# --- the loop around it -----------------------------------------------------

class FakeBot:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []

    async def send_message(self, chat_id: int, text: str, **kw) -> None:
        self.sent.append((chat_id, text))


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(repos_base, "DB_PATH", str(tmp_path / "bot_data.db"))
    asyncio.run(init_db())


@pytest.fixture()
def clock(monkeypatch):
    """A watchdog that wakes instantly and a clock the test moves by hand."""
    import bot.sync as module

    box = {"now": NOW, "rounds": 0, "limit": 1}

    class FakeDatetime:
        @staticmethod
        def now(tz=None):
            return box["now"]

    async def fake_sleep(_seconds: float) -> None:
        box["rounds"] += 1
        if box["rounds"] > box["limit"]:
            raise asyncio.CancelledError

    monkeypatch.setattr(module, "datetime", FakeDatetime)
    monkeypatch.setattr(module.asyncio, "sleep", fake_sleep)
    return box


def _run_watchdog(bot: FakeBot) -> None:
    async def go() -> None:
        with pytest.raises(asyncio.CancelledError):
            await watch_for_silence(bot, [ADMIN])

    asyncio.run(go())


def test_a_fresh_sync_says_nothing(db, clock):
    asyncio.run(sync_state.finish_success(SOURCE, "2026-08-04 16:19:00"))
    bot = FakeBot()
    _run_watchdog(bot)
    assert bot.sent == []


def test_a_silent_sync_reaches_the_admin(db, clock):
    """The row is stamped by the database clock, so the test moves the process
    clock forward instead of pretending the row is old."""
    asyncio.run(sync_state.finish_success(SOURCE, "2026-08-04 16:00:00"))
    clock["now"] = datetime.now(timezone.utc) + SILENCE_AFTER + timedelta(minutes=1)

    bot = FakeBot()
    _run_watchdog(bot)

    assert len(bot.sent) == 1
    assert bot.sent[0][0] == ADMIN
    assert "has not succeeded" in bot.sent[0][1]


def test_a_sync_that_stays_broken_is_not_reported_every_minute(db, clock):
    """An alert repeating faster than anybody can act on it is how alerts get
    muted, and a muted alert looks like coverage."""
    asyncio.run(sync_state.finish_success(SOURCE, "2026-08-04 16:00:00"))
    clock["now"] = datetime.now(timezone.utc) + SILENCE_AFTER + timedelta(minutes=1)
    clock["limit"] = 5

    bot = FakeBot()
    _run_watchdog(bot)

    assert len(bot.sent) == 1


def test_it_speaks_up_again_after_an_hour(db, clock):
    asyncio.run(sync_state.finish_success(SOURCE, "2026-08-04 16:00:00"))
    base = datetime.now(timezone.utc) + SILENCE_AFTER + timedelta(minutes=1)
    clock["now"] = base
    clock["limit"] = 2

    bot = FakeBot()

    import bot.sync as module
    original_sleep = module.asyncio.sleep

    async def sleep_and_age(seconds: float) -> None:
        clock["now"] = clock["now"] + REALERT_AFTER
        await original_sleep(seconds)

    module.asyncio.sleep = sleep_and_age
    try:
        _run_watchdog(bot)
    finally:
        module.asyncio.sleep = original_sleep

    assert len(bot.sent) == 2


def test_recovery_is_reported_once(db, clock):
    """So that "it is still broken" and "somebody fixed it" are distinguishable
    without going to look."""
    asyncio.run(sync_state.finish_success(SOURCE, "2026-08-04 16:00:00"))
    clock["now"] = datetime.now(timezone.utc) + SILENCE_AFTER + timedelta(minutes=1)
    clock["limit"] = 3

    bot = FakeBot()

    import bot.sync as module
    original_sleep = module.asyncio.sleep
    state = {"rounds": 0}

    async def sleep_and_recover(seconds: float) -> None:
        # Second round, not the first: the sleep runs before each check, so
        # recovering on the first one would mean the watchdog never saw the
        # outage and had nothing to report as fixed.
        state["rounds"] += 1
        if state["rounds"] == 2:
            await sync_state.finish_success(SOURCE, "2026-08-04 16:40:00")
            clock["now"] = datetime.now(timezone.utc)
        await original_sleep(seconds)

    module.asyncio.sleep = sleep_and_recover
    try:
        _run_watchdog(bot)
    finally:
        module.asyncio.sleep = original_sleep

    assert len(bot.sent) == 2
    assert "is back" in bot.sent[1][1]


def test_without_admins_it_says_so_and_stops(db, clock):
    """Rather than looping forever with nowhere to send. A stalled sync nobody
    is told about is the state this whole file exists to prevent."""
    async def go() -> None:
        await watch_for_silence(FakeBot(), [])

    asyncio.run(go())


# --- the customer-facing half ----------------------------------------------

def test_a_healthy_sync_shows_no_notice():
    state = {"last_success_at": "2026-08-04 16:18:00"}
    assert data_age(state, None, now=NOW) == timedelta(minutes=2)


def test_a_customer_whose_orders_never_change_is_not_told_they_are_stale():
    """The sweep writes only what changed, so somebody who last ordered in
    January has a January synced_at while the sync is perfectly healthy. Reading
    that alone would put a stale warning on a current list."""
    state = {"last_success_at": "2026-08-04 16:18:00"}
    assert data_age(state, "2026-01-04 10:00:00", now=NOW) == timedelta(minutes=2)


def test_a_dead_sweep_is_covered_by_this_chat_s_own_refresh():
    """And the other way round: the on-demand refresh keeps one customer current
    while the sweep is dead, which the sweep's own timestamp cannot see."""
    state = {"last_success_at": "2026-08-01 10:00:00"}
    assert data_age(state, "2026-08-04 16:15:00", now=NOW) == timedelta(minutes=5)


def test_nothing_ever_synced_is_not_a_staleness():
    """That is the empty screen, and it says so in its own words."""
    assert data_age(None, None, now=NOW) is None


def test_the_notice_appears_only_past_the_threshold(db, monkeypatch):
    from bot import sync as module

    async def no_chat_sync(chat_id: int):
        return None

    monkeypatch.setattr(module, "get_last_sync_time", no_chat_sync)
    asyncio.run(sync_state.finish_success(SOURCE, "2026-08-04 16:00:00"))

    t = customer_texts("uk")
    fresh = asyncio.run(module.stale_notice(555, t, now=datetime.now(timezone.utc)))
    assert fresh == ""

    stale_at = datetime.now(timezone.utc) + timedelta(hours=3, minutes=1)
    warned = asyncio.run(module.stale_notice(555, t, now=stale_at))
    assert "3" in warned
