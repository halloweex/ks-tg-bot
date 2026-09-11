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

from loguru import logger

from core.domain.campaign import parse
from core.ports.analytics import CampaignOutcomes, UsageStats

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


# Campaigns the bot sends *because* somebody bought something: Rivo awards
# points for an order, a referral pays out when the friend's order lands. For
# these the "before" window is not a background rate — the order that triggered
# the message is sitting in it by construction, so the row would read as "she
# would have bought anyway" about the very purchase that caused the send.
#
# Rendered as unknown rather than dropped: the send is still worth counting,
# only its comparison is meaningless. Which is the same discipline the click
# column would need if it existed, and the reason there is no click column.
CAUSED_BY_A_PURCHASE: frozenset[str] = frozenset({"loyalty", "referral"})

# Below this many recipients a percentage is arithmetic dressed as a finding.
# Birthdays reach one to three people on an ordinary day; "1 of 2 ordered" is a
# fact about one woman and "50%" is a claim about a channel.
MIN_FOR_A_SHARE: int = 10


@dataclass(frozen=True)
class CampaignRow:
    """One run of one campaign, and what happened in the days after it.

    `share` and `share_before` are None rather than 0.0 wherever a percentage
    would mislead — too few recipients to divide, or a campaign whose "before"
    window cannot mean what it looks like. The renderer prints those as "—",
    because a zero invents a failure and a dash reports an absence of knowledge.
    """

    kind: str
    ref: str
    key: str
    type: str
    people: int
    delivered: int
    failed: int
    bought: int
    bought_before: int | None
    last_sent: str

    @property
    def share(self) -> float | None:
        if self.people < MIN_FOR_A_SHARE:
            return None
        return 100.0 * self.bought / self.people

    @property
    def share_before(self) -> float | None:
        if self.bought_before is None or self.people < MIN_FOR_A_SHARE:
            return None
        return 100.0 * self.bought_before / self.people


@dataclass(frozen=True)
class CampaignReport:
    days: int
    window_days: int
    rows: list[CampaignRow] = field(default_factory=list)
    #: Keys read out of the outbox that are not campaign keys at all. Counted
    #: rather than raised on: a malformed key means a row written by code that
    #: no longer exists, and one of those must not cost the owner the whole
    #: report. Counted rather than ignored, because a number climbing here is
    #: how a sender that stopped spelling its key properly becomes visible.
    unreadable: int = 0


async def campaign_report(
    store: CampaignOutcomes, *, days: int = 30, window_days: int = 7,
) -> CampaignReport:
    """What the bot sent, and what the people who got it did next.

    The question §6.4 was written for — "did Tuesday's broadcast sell
    anything?" — minus the middle of its funnel, which this bot cannot observe.
    See `core/ports/analytics.py::CampaignOutcomes` for why there is no click,
    and `core/repos/campaigns.py` for why there is no revenue.

    `parse` is what turns the stored string back into a key here, and this is
    its first real caller. Its docstring always said "out of a callback or a
    database row"; only the second half was ever reachable.
    """
    rows: list[CampaignRow] = []
    unreadable = 0
    for (key, type_, people, delivered, failed, bought,
         bought_before, last_sent) in await store.outcomes(
            days=days, window_days=window_days,
            exclude_types=("support", "broadcast_report")):
        try:
            campaign = parse(key)
        except ValueError:
            # Deliberately not fatal. `parse` raises so a malformed key cannot
            # be silently counted as an unnamed campaign; here that means one
            # bad row, not one bad report.
            logger.warning("Campaign report: {!r} is not a campaign key", key)
            unreadable += 1
            continue
        rows.append(CampaignRow(
            kind=campaign.kind, ref=campaign.ref, key=key, type=type_,
            people=people, delivered=delivered, failed=failed, bought=bought,
            bought_before=(None if campaign.kind in CAUSED_BY_A_PURCHASE
                           else bought_before),
            last_sent=last_sent or "",
        ))
    return CampaignReport(days=days, window_days=window_days, rows=rows,
                          unreadable=unreadable)
