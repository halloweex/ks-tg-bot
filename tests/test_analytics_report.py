"""The two divisions in /stats, and what they do when the denominator is zero.

Both used to sit inline in the broadcast handler, where reaching them meant
sending a Telegram command as an admin. They are the arithmetic the whole
readout rests on: a funnel share against nobody, and a miss rate against no
lookups.

The store arrives as an object implementing core.ports.analytics.UsageStats
rather than as four patched module attributes. The difference is not style:
patching names inside the scenario cannot notice a signature drifting apart
from the repository it stands for, and a stub that has to satisfy the same
protocol can.
"""
from __future__ import annotations

import asyncio
from collections.abc import Sequence

import pytest

from core.ports.analytics import UsageStats
from core.repos.events import SqliteUsageStats
from core.usecases.analytics import FUNNEL_STEPS, usage_report


class FakeStats:
    """Four canned answers, and a record of what was asked for."""

    def __init__(self) -> None:
        self.funnel = {"start": 100, "contact_shared": 50, "registered": 40,
                       "orders_viewed": 25}
        self.miss = (3, 12)          # (found nothing, total lookups)
        self.returning = (7, 30)     # (on more than one day, active at all)
        self.events = [("start", 120, 100), ("orders_viewed", 60, 25)]
        self.asked_steps: Sequence[str] | None = None
        self.asked_days: list[int] = []
        self.asked_event_days: int | None = None

    async def users_reaching(
        self, steps: Sequence[str], *, days: int
    ) -> dict[str, int]:
        self.asked_steps = steps
        self.asked_days.append(days)
        return dict(self.funnel)

    async def order_lookup_misses(self, *, days: int) -> tuple[int, int]:
        self.asked_days.append(days)
        return self.miss

    async def retention(self, *, days: int) -> tuple[int, int]:
        self.asked_days.append(days)
        return self.returning

    async def busiest_events(self, *, days: int) -> list[tuple[str, int, int]]:
        self.asked_event_days = days
        return list(self.events)


@pytest.fixture()
def stats() -> FakeStats:
    return FakeStats()


def test_the_stub_and_the_real_store_answer_the_same_port(stats):
    """Both sides of the seam satisfy it, or the stub is testing a fiction."""
    assert isinstance(stats, UsageStats)
    assert isinstance(SqliteUsageStats(), UsageStats)


def test_the_funnel_keeps_its_order_and_its_shares(stats):
    report = asyncio.run(usage_report(stats))
    assert [s.key for s in report.funnel] == list(FUNNEL_STEPS)
    assert [s.users for s in report.funnel] == [100, 50, 40, 25]
    assert [s.share for s in report.funnel] == [100.0, 50.0, 40.0, 25.0]


def test_the_report_decides_which_steps_make_a_funnel(stats):
    """The steps travel from the scenario to the store, rather than being
    written down in both. A store answering about its own private list would
    print a funnel the report never asked for."""
    asyncio.run(usage_report(stats))
    assert list(stats.asked_steps) == list(FUNNEL_STEPS)


def test_an_empty_funnel_has_no_shares_rather_than_zero_ones(stats):
    """"Nobody started" and "nobody got here" are different facts, and rendering
    the second as 0% invents a drop-off that did not happen."""
    stats.funnel = {}
    report = asyncio.run(usage_report(stats))
    assert [s.users for s in report.funnel] == [0, 0, 0, 0]
    assert all(step.share is None for step in report.funnel)


def test_a_step_nobody_reached_is_still_reported(stats):
    """A missing key is zero, not an absent row: a funnel with a step silently
    dropped reads as if the step did not exist."""
    stats.funnel = {"start": 10}
    report = asyncio.run(usage_report(stats))
    assert [s.users for s in report.funnel] == [10, 0, 0, 0]
    assert report.funnel[1].share == 0.0


def test_the_miss_rate(stats):
    report = asyncio.run(usage_report(stats))
    assert report.lookups == 12
    assert report.lookups_without_orders == 3
    assert report.miss_rate == 25.0


def test_no_lookups_yet_is_not_a_zero_percent_miss_rate(stats):
    """Zero would read as "the phone match works perfectly", which is the
    opposite of "nobody has tried"."""
    stats.miss = (0, 0)
    report = asyncio.run(usage_report(stats))
    assert report.miss_rate is None


def test_retention_is_not_transposed(stats):
    """The store returns (returning, active) and the report names them, so the
    pair cannot quietly swap: 7 of 30 came back, not 30 of 7."""
    report = asyncio.run(usage_report(stats))
    assert (report.returning_users, report.active_users) == (7, 30)


def test_the_windows_travel_with_the_report(stats):
    """The handler prints "Last N days" from the report rather than repeating a
    literal, so the two cannot disagree — and the window the store is asked for
    is the same one the caption will carry."""
    report = asyncio.run(usage_report(stats, days=90, event_days=14))
    assert (report.days, report.event_days) == (90, 14)
    assert set(stats.asked_days) == {90}
    assert stats.asked_event_days == 14


def test_events_come_through_as_they_are(stats):
    report = asyncio.run(usage_report(stats))
    assert report.events == [("start", 120, 100), ("orders_viewed", 60, 25)]

    stats.events = []
    assert asyncio.run(usage_report(stats)).events == []
