"""The activity readout: who was in the bot, by the shop's day.

Read against a real temporary database, because the two places this report can
lie are both below the arithmetic: the SQL that decides who counts, and the
step from a UTC timestamp to a Kyiv date. The second one is pinned on the night
the clocks went back — 2026-10-25, 04:00 Kyiv summer time became 03:00 winter
time — because a hard-coded +3 passes every test written in July.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

import pytest

from core.ports.analytics import ActivityLog
from core.repos import base as repos_base
from core.repos.events import SqliteActivityLog
from core.repos.schema import init_db
from core.usecases.analytics import MIN_FOR_A_SHARE, activity_report

ADMIN = 4242

# Noon in Kyiv on the day after the clocks went back.
NOW = datetime(2026, 10, 26, 10, 0, tzinfo=timezone.utc)


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(repos_base, "DB_PATH", str(tmp_path / "bot_data.db"))
    asyncio.run(init_db())


def _event(chat_id: int | None, at: str, event: str = "start") -> None:
    async def go() -> None:
        async with repos_base.connect() as conn:
            await conn.execute(
                "INSERT INTO events (chat_id, event, meta, created_at) "
                "VALUES (?, ?, '{}', ?)", (chat_id, event, at))
            await conn.commit()
    asyncio.run(go())


def _registered(chat_id: int, at: str) -> None:
    async def go() -> None:
        async with repos_base.connect() as conn:
            await conn.execute(
                "INSERT INTO users (chat_id, phone, created_at) VALUES (?, ?, ?)",
                (chat_id, f"+38050{chat_id:07d}", at))
            await conn.commit()
    asyncio.run(go())


def _report(days: int = 14, **kw):
    kw.setdefault("now", NOW)
    return asyncio.run(activity_report(SqliteActivityLog(), days=days, **kw))


def _day(report, iso: str):
    [row] = [d for d in report.daily if d.day == iso]
    return row


def test_the_store_answers_the_port():
    assert isinstance(SqliteActivityLog(), ActivityLog)


# --- the shop's day, not the server's ----------------------------------------

def test_a_summer_evening_after_21_utc_is_already_tomorrow_in_kyiv(db):
    _event(1, "2026-10-23 21:30:00")       # 00:30 on the 24th, summer time
    report = _report()
    assert _day(report, "2026-10-24").active == 1
    assert _day(report, "2026-10-23").active == 0


def test_after_the_clocks_go_back_the_day_turns_at_22_utc(db):
    """A fixed +3 puts both of these on the 26th."""
    _event(1, "2026-10-25 21:30:00")       # 23:30 on the 25th, winter time
    _event(2, "2026-10-25 22:30:00")       # 00:30 on the 26th
    report = _report()
    assert _day(report, "2026-10-25").active == 1
    assert _day(report, "2026-10-26").active == 1


# --- who counts --------------------------------------------------------------

def test_a_person_is_counted_once_a_day_however_often_they_tap(db):
    for minute in range(10, 50, 10):
        _event(1, f"2026-10-24 09:{minute}:00", "orders_viewed")
    _event(1, "2026-10-24 15:00:00", "support_opened")
    assert _day(_report(), "2026-10-24").active == 1


def test_the_admins_and_the_bots_own_bookkeeping_do_not_count(db):
    _event(ADMIN, "2026-10-24 09:00:00")
    _registered(ADMIN, "2026-10-24 09:01:00")
    _event(None, "2026-10-24 09:00:00", "rivo_webhook_arrived")
    _event(1, "2026-10-24 09:00:00")

    report = _report(exclude=[ADMIN])
    assert _day(report, "2026-10-24").active == 1
    assert report.users_total == 1
    assert report.registered_total == 0


def test_registrations_come_from_the_registration_row_not_the_event(db):
    """The `registered` event is fire-and-forget and can be lost; the row is
    what registration is."""
    _event(1, "2026-10-24 09:00:00")
    _registered(1, "2026-10-24 09:02:00")          # no `registered` event
    assert _day(_report(), "2026-10-24").registered == 1


def test_somebody_known_only_from_before_the_log_is_still_a_user(db):
    """Registrations started two days before the event log did."""
    _registered(1, "2026-07-28 13:33:02")
    _event(2, "2026-10-24 09:00:00")
    report = _report()
    assert report.users_total == 2
    assert report.log_starts == "2026-10-24"


# --- the windows ---------------------------------------------------------------

def test_the_series_is_the_last_n_days_ending_today(db):
    report = _report(days=14)
    assert len(report.daily) == 14
    assert report.daily[-1].day == "2026-10-26"
    assert report.daily[0].day == "2026-10-13"


def test_weekly_and_monthly_active_count_people_and_not_days(db):
    _event(1, "2026-10-26 08:00:00")               # today
    _event(1, "2026-10-21 08:00:00")               # same person, 5 days ago
    _event(2, "2026-10-15 08:00:00")               # 11 days ago
    _event(3, "2026-09-20 08:00:00")               # 36 days ago: neither
    report = _report()
    assert (report.wau, report.mau) == (1, 2)


def test_registrations_this_week_are_compared_with_the_week_before(db):
    _registered(1, "2026-10-26 08:00:00")
    _registered(2, "2026-10-20 08:00:00")          # 6 days ago: this week
    _registered(3, "2026-10-19 08:00:00")          # 7 days ago: the one before
    report = _report()
    assert (report.registered_7d, report.registered_prev_7d) == (2, 1)


def test_a_week_splits_into_new_and_returning_and_they_add_up(db):
    _event(1, "2026-10-01 08:00:00")               # first seen weeks ago
    _event(1, "2026-10-22 08:00:00")               # back this week
    _event(2, "2026-10-21 08:00:00")               # new this week
    report = _report()
    week = report.weekly[-2]                       # Mon 19 – Sun 25 Oct
    assert week.week == "2026-10-19"
    assert (week.active, week.new, week.returning) == (2, 1, 1)
    assert all(w.new + w.returning == w.active for w in report.weekly)


# --- the shares it refuses to print -------------------------------------------

def test_no_share_is_printed_over_a_handful_of_people(db):
    for chat in range(1, 4):
        _event(chat, "2026-10-10 08:00:00")
        _registered(chat, "2026-10-10 08:05:00")
    report = _report()
    assert report.activation_cohort == 3
    assert report.activation is None
    assert report.stickiness is None


def test_activation_is_registering_within_a_week_of_arriving(db):
    """Cohort: first seen 7–35 days ago, so everybody in it had the full week."""
    for chat in range(1, MIN_FOR_A_SHARE + 1):
        _event(chat, "2026-10-10 08:00:00")
    for chat in range(1, 6):
        _registered(chat, "2026-10-12 08:00:00")   # two days later: activated
    _registered(6, "2026-10-20 08:00:00")          # ten days later: too late
    _event(99, "2026-10-24 08:00:00")              # arrived 2 days ago: not yet

    report = _report()
    assert report.activation_cohort == MIN_FOR_A_SHARE
    assert report.activation_registered == 5
    assert report.activation == pytest.approx(50.0)


def test_an_empty_database_answers_with_zeros_and_no_shares(db):
    report = _report()
    assert report.users_total == report.wau == report.mau == 0
    assert report.log_starts is None
    assert report.activation is None and report.stickiness is None
    assert all(d.active == 0 for d in report.daily)


# --- which press people leave after ------------------------------------------

def _tap(chat_id: int, at: str, what: str, label: str = "") -> None:
    async def go() -> None:
        async with repos_base.connect() as conn:
            await conn.execute(
                "INSERT INTO events (chat_id, event, meta, created_at) VALUES (?, 'tap', ?, ?)",
                (chat_id, json.dumps({"what": what, "label": label or what}), at))
            await conn.commit()
    asyncio.run(go())


def _row(report, what):
    [row] = [r for r in report.taps if r.what == what]
    return row


def test_a_visit_ends_on_the_last_press_before_half_an_hour_of_nothing(db):
    _tap(1, "2026-10-24 09:00:00", "menu:open_orders", "📦 Замовлення")
    _tap(1, "2026-10-24 09:05:00", "ord:open", "Замовлення ##.##")
    _tap(1, "2026-10-24 12:00:00", "menu:open_favourites")     # a new visit
    report = _report()
    assert _row(report, "ord:open").session_exits == 1
    assert _row(report, "menu:open_orders").session_exits == 0
    assert _row(report, "menu:open_favourites").session_exits == 1
    assert _row(report, "ord:open").label == "Замовлення ##.##"


def test_somebody_silent_for_a_week_left_after_their_last_press(db):
    _tap(1, "2026-10-15 09:00:00", "menu:open_orders")
    _tap(1, "2026-10-15 09:01:00", "menu:open_info")             # left after this
    _tap(2, "2026-10-15 09:00:00", "menu:open_info")
    _tap(2, "2026-10-25 09:00:00", "menu:open_orders")           # still here
    report = _report()
    assert _row(report, "menu:open_info").left_after == 1
    assert _row(report, "menu:open_orders").left_after == 0
    assert report.left_total == 1
    assert report.taps[0].what == "menu:open_info", "the rows people leave after come first"


def test_any_later_event_means_they_did_not_leave(db):
    """A press is the last *press*; an inline panel opened later is still use."""
    _tap(1, "2026-10-15 09:00:00", "menu:open_info")
    _event(1, "2026-10-24 09:00:00", "favourites_inline_opened")
    assert _report().left_total == 0


def test_presses_and_people_are_counted_apart(db):
    for minute in range(3):
        _tap(1, f"2026-10-24 09:0{minute}:00", "ord:page")
    _tap(2, "2026-10-24 09:00:00", "ord:page")
    row = _row(_report(), "ord:page")
    assert (row.presses, row.people) == (4, 2)


def test_the_press_table_says_when_recording_began(db):
    assert _report().taps_since is None
    _tap(1, "2026-10-25 12:00:00", "/start")
    assert _report().taps_since == "2026-10-25"


def test_presses_outside_the_window_are_not_counted(db):
    _tap(1, "2026-09-01 09:00:00", "menu:open_info")
    assert _report(days=14).taps == []


# --- the entry funnel ------------------------------------------------------------

def test_the_entry_funnel_follows_one_newcomer_to_each_step(db):
    # 1: all the way, and back on another day
    _event(1, "2026-10-20 09:00:00", "start")
    _event(1, "2026-10-20 09:00:30", "contact_shared")
    _registered(1, "2026-10-20 09:00:31")
    _tap(1, "2026-10-20 09:01:00", "menu:open_orders")
    _tap(1, "2026-10-22 18:00:00", "key:orders")
    # 2: registered, then nothing but the menu that registration draws
    _event(2, "2026-10-21 09:00:00", "start")
    _event(2, "2026-10-21 09:00:20", "contact_shared")
    _registered(2, "2026-10-21 09:00:21")
    # 3: typed the number, never pressed the button
    _event(3, "2026-10-21 10:00:00", "start")
    _event(3, "2026-10-21 10:00:30", "contact_rejected")
    # 4: pressed /start and nothing else
    _event(4, "2026-10-22 10:00:00", "start")
    steps = {s.key: s.people for s in _report().entry}
    assert steps == {"started": 4, "touched_share": 3, "registered": 2,
                     "acted": 1, "came_back": 1}


def test_somebody_first_seen_before_the_window_is_not_a_newcomer(db):
    _event(1, "2026-08-01 09:00:00", "start")
    _event(1, "2026-10-24 09:00:00", "start")
    assert {s.key: s.people for s in _report(days=14).entry}["started"] == 0
