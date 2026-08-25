"""The numbers behind /stats, without the sentences that carry them.

Section 7 of docs/architecture.md puts this here, and the reason is not tidiness:
the same four questions are the ones the web cabinet will ask, and the ones a
weekly digest would send. Left in the broadcast handler they were reachable only
by an admin typing a command into Telegram.

What moved is the gathering and the arithmetic — which is to say, the two
divisions that can divide by zero. What stayed is the wording, because "shared
contact" and "^ Telegram phone did not match the one in the CRM" are a readout
written for one person to read in a chat.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from core.ports.analytics import UsageStats

# The onboarding funnel, in order. Order matters: each step is a subset of the
# one above it, and a share is only meaningful against the top.
FUNNEL_STEPS: tuple[str, ...] = ("start", "contact_shared", "registered",
                                 "orders_viewed")


@dataclass(frozen=True)
class FunnelStep:
    key: str
    users: int
    #: Percentage of the top of the funnel, or None when the top is empty —
    #: not 0.0, because "nobody started" and "nobody got here" are different
    #: facts and rendering the second as 0% invents a drop-off that did not
    #: happen.
    share: float | None


@dataclass(frozen=True)
class UsageReport:
    days: int
    event_days: int
    funnel: list[FunnelStep] = field(default_factory=list)
    lookups: int = 0
    lookups_without_orders: int = 0
    #: Share of lookups that found nothing, or None when nobody looked yet.
    #: This is the number that says whether matching customers by phone works
    #: at all — see docs/found-during-move.md and core/repos/events.py.
    miss_rate: float | None = None
    active_users: int = 0
    returning_users: int = 0
    #: (event, occurrences, distinct users), busiest first.
    events: list[tuple[str, int, int]] = field(default_factory=list)


async def usage_report(
    stats: UsageStats, *, days: int = 30, event_days: int = 7
) -> UsageReport:
    """Everything /stats reports, gathered and divided.

    The store arrives as an argument rather than as an import. What that buys
    here is a test without a database — the four numbers can be handed in — and
    what it buys later is an engine change that does not reach into this file.
    """
    funnel_users = await stats.users_reaching(FUNNEL_STEPS, days=days)
    misses, lookups = await stats.order_lookup_misses(days=days)
    returning, active = await stats.retention(days=days)
    counts = await stats.busiest_events(days=event_days)

    top = funnel_users.get(FUNNEL_STEPS[0], 0)
    funnel = [
        FunnelStep(
            key=step,
            users=funnel_users.get(step, 0),
            share=(100 * funnel_users.get(step, 0) / top) if top else None,
        )
        for step in FUNNEL_STEPS
    ]

    return UsageReport(
        days=days,
        event_days=event_days,
        funnel=funnel,
        lookups=lookups,
        lookups_without_orders=misses,
        miss_rate=(100 * misses / lookups) if lookups else None,
        active_users=active,
        returning_users=returning,
        events=list(counts),
    )
