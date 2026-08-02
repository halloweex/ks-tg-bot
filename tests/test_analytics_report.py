"""The two divisions in /stats, and what they do when the denominator is zero.

Both used to sit inline in the broadcast handler, where reaching them meant
sending a Telegram command as an admin. They are the arithmetic the whole
readout rests on: a funnel share against nobody, and a miss rate against no
lookups.
"""
from __future__ import annotations

import asyncio

import pytest

from core.usecases import analytics as module
from core.usecases.analytics import FUNNEL_STEPS, usage_report


@pytest.fixture()
def crm(monkeypatch):
    """Replaces the four repository calls; the arithmetic is what is left."""
    data = {
        "funnel": {"start": 100, "contact_shared": 50, "registered": 40,
                   "orders_viewed": 25},
        "miss": (3, 12),          # (found nothing, total lookups)
        "returning": (7, 30),     # (on more than one day, active at all)
        "events": [("start", 120, 100), ("orders_viewed", 60, 25)],
    }

    async def funnel_counts(days):
        return dict(data["funnel"])

    async def lookup_miss_rate(days):
        return data["miss"]

    async def returning_users(days):
        return data["returning"]

    async def event_counts(days):
        return list(data["events"])

    monkeypatch.setattr(module, "funnel_counts", funnel_counts)
    monkeypatch.setattr(module, "lookup_miss_rate", lookup_miss_rate)
    monkeypatch.setattr(module, "returning_users", returning_users)
    monkeypatch.setattr(module, "event_counts", event_counts)
    return data


def test_the_funnel_keeps_its_order_and_its_shares(crm):
    report = asyncio.run(usage_report())
    assert [s.key for s in report.funnel] == list(FUNNEL_STEPS)
    assert [s.users for s in report.funnel] == [100, 50, 40, 25]
    assert [s.share for s in report.funnel] == [100.0, 50.0, 40.0, 25.0]


def test_an_empty_funnel_has_no_shares_rather_than_zero_ones(crm):
    """"Nobody started" and "nobody got here" are different facts, and rendering
    the second as 0% invents a drop-off that did not happen."""
    crm["funnel"] = {}
    report = asyncio.run(usage_report())
    assert [s.users for s in report.funnel] == [0, 0, 0, 0]
    assert all(step.share is None for step in report.funnel)


def test_a_step_nobody_reached_is_still_reported(crm):
    """A missing key is zero, not an absent row: a funnel with a step silently
    dropped reads as if the step did not exist."""
    crm["funnel"] = {"start": 10}
    report = asyncio.run(usage_report())
    assert [s.users for s in report.funnel] == [10, 0, 0, 0]
    assert report.funnel[1].share == 0.0


def test_the_miss_rate(crm):
    report = asyncio.run(usage_report())
    assert report.lookups == 12
    assert report.lookups_without_orders == 3
    assert report.miss_rate == 25.0


def test_no_lookups_yet_is_not_a_zero_percent_miss_rate(crm):
    """Zero would read as "the phone match works perfectly", which is the
    opposite of "nobody has tried"."""
    crm["miss"] = (0, 0)
    report = asyncio.run(usage_report())
    assert report.miss_rate is None


def test_retention_is_not_transposed(crm):
    """The repository returns (returning, active) and the report names them, so
    the pair cannot quietly swap: 7 of 30 came back, not 30 of 7."""
    report = asyncio.run(usage_report())
    assert (report.returning_users, report.active_users) == (7, 30)


def test_the_windows_travel_with_the_report(crm):
    """The handler prints "Last N days" from the report rather than repeating a
    literal, so the two cannot disagree."""
    report = asyncio.run(usage_report(days=90, event_days=14))
    assert (report.days, report.event_days) == (90, 14)


def test_events_come_through_as_they_are(crm):
    report = asyncio.run(usage_report())
    assert report.events == [("start", 120, 100), ("orders_viewed", 60, 25)]

    crm["events"] = []
    assert asyncio.run(usage_report()).events == []
