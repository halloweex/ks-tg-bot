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

from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone

from loguru import logger

from core.domain.campaign import parse
from core.domain.quiet import SHOP_TZ
from core.ports.analytics import ActivityLog, CampaignOutcomes, UsageStats

# The onboarding funnel, in order, over the people first seen in the window
# (see UsageStats.users_reaching). Order matters: each step is a subset of the
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


# --------------- Activity: who was here, by the shop's day ---------------
#
# What the dashboard in the neighbouring analytics service draws, and what the
# Activity section of /stats prints, so the two cannot disagree: one function,
# one set of windows, one clock.
#
# **Days are Kyiv days.** The customers are in Ukraine and every dashboard in
# the analytics service buckets by Kyiv date; a UTC day would put a customer's
# evening into tomorrow for three hours of every day. The store holds UTC and
# has no time zones, so the bucketing happens here — see the port for why the
# rows arrive per hour.

#: Bumped when a field changes meaning or disappears. Adding a field is not a
#: change: the reader ignores what it does not know.
ACTIVITY_VERSION: int = 1

WAU_DAYS: int = 7
MAU_DAYS: int = 30
#: A newcomer counts as activated if they registered within this many days of
#: their first trace.
ACTIVATION_DAYS: int = 7
#: Whose activation is measured: people first seen between ACTIVATION_DAYS and
#: ACTIVATION_DAYS + ACTIVATION_COHORT_DAYS ago. The inner edge is there so that
#: everybody in the cohort has had the full window to register — a person who
#: arrived an hour ago has not failed to sign up yet.
ACTIVATION_COHORT_DAYS: int = 28


@dataclass(frozen=True)
class ActivityDay:
    #: Kyiv date, ISO.
    day: str
    #: Distinct people with any event that day.
    active: int
    #: People whose first trace fell on that day.
    new: int
    #: Registrations that day.
    registered: int


@dataclass(frozen=True)
class ActivityWeek:
    #: The Monday the Kyiv week starts on, ISO. The current week is partial.
    week: str
    active: int
    #: Active that week and first seen that week.
    new: int
    #: Active that week and first seen before it. `new + returning == active`.
    returning: int


@dataclass(frozen=True)
class ActivityReport:
    version: int
    #: UTC, ISO with a Z: the moment "today" was decided.
    generated_at: str
    tz: str
    days: int
    #: Kyiv date of the first event that carries a person, or None. Activity
    #: before it was not recorded, which is not the same as not having happened.
    log_starts: str | None
    users_total: int
    registered_total: int
    registered_7d: int
    registered_prev_7d: int
    wau_days: int
    wau: int
    mau_days: int
    mau: int
    #: 100 × WAU / MAU, or None while MAU is too small to divide.
    stickiness: float | None
    activation_days: int
    activation_cohort_days: int
    activation_cohort: int
    activation_registered: int
    #: Percentage, or None while the cohort is under MIN_FOR_A_SHARE.
    activation: float | None
    daily: list[ActivityDay] = field(default_factory=list)
    weekly: list[ActivityWeek] = field(default_factory=list)


def _utc(raw: str | None) -> datetime | None:
    """A stored timestamp or hour ('YYYY-MM-DD HH') as an aware UTC datetime."""
    if not raw:
        return None
    text = str(raw).strip().replace("Z", "")
    if len(text) == len("YYYY-MM-DD HH"):
        text += ":00:00"
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        logger.warning("Activity report could not read a timestamp: {!r}", raw)
        return None
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def _shop_day(moment: datetime) -> date:
    return moment.astimezone(SHOP_TZ).date()


def _share(part: int, whole: int) -> float | None:
    return 100.0 * part / whole if whole >= MIN_FOR_A_SHARE else None


async def activity_report(
    log: ActivityLog, *, days: int, exclude: Sequence[int] = (),
    now: datetime | None = None,
) -> ActivityReport:
    """Daily and weekly activity over the last `days` Kyiv days, and the totals.

    "Last N days" is N calendar days ending with today, today included and
    partial. `exclude` is the people who must not count — the admins.
    """
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    today = _shop_day(now)
    first_day = today - timedelta(days=days - 1)
    first_week = first_day - timedelta(days=first_day.weekday())
    earliest = min(first_week, today - timedelta(days=MAU_DAYS - 1))
    since = (datetime.combine(earliest, time(0), tzinfo=SHOP_TZ)
             .astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"))

    active_on: dict[date, set[int]] = defaultdict(set)
    for chat_id, hour in await log.active_hours(since=since, exclude=exclude):
        moment = _utc(hour)
        if moment is not None:
            active_on[_shop_day(moment)].add(chat_id)

    seen = {chat_id: moment for chat_id, raw in await log.first_seen(exclude=exclude)
            if (moment := _utc(raw)) is not None}
    regs = {chat_id: moment for chat_id, raw in await log.registered(exclude=exclude)
            if (moment := _utc(raw)) is not None}
    starts = _utc(await log.log_starts())

    seen_day = {chat_id: _shop_day(moment) for chat_id, moment in seen.items()}
    new_on = Counter(seen_day.values())
    registered_on = Counter(_shop_day(moment) for moment in regs.values())

    def active_between(start: date, end: date) -> set[int]:
        people: set[int] = set()
        day = start
        while day <= end:
            people |= active_on.get(day, set())
            day += timedelta(days=1)
        return people

    def registered_between(start: date, end: date) -> int:
        return sum(n for day, n in registered_on.items() if start <= day <= end)

    daily = [
        ActivityDay(day=day.isoformat(), active=len(active_on.get(day, ())),
                    new=new_on.get(day, 0), registered=registered_on.get(day, 0))
        for day in (first_day + timedelta(days=i) for i in range(days))
    ]

    weekly: list[ActivityWeek] = []
    monday = first_week
    while monday <= today:
        sunday = monday + timedelta(days=6)
        people = active_between(monday, min(sunday, today))
        new = sum(1 for chat_id in people
                  if chat_id in seen_day and monday <= seen_day[chat_id] <= sunday)
        weekly.append(ActivityWeek(week=monday.isoformat(), active=len(people),
                                   new=new, returning=len(people) - new))
        monday += timedelta(days=7)

    wau = len(active_between(today - timedelta(days=WAU_DAYS - 1), today))
    mau = len(active_between(today - timedelta(days=MAU_DAYS - 1), today))

    # Only people first seen after the log began: before it, the earliest trace
    # anybody has is their registration, so every one of them would count as
    # registered on the spot.
    window = timedelta(days=ACTIVATION_DAYS)
    oldest = now - timedelta(days=ACTIVATION_DAYS + ACTIVATION_COHORT_DAYS)
    cohort = [chat_id for chat_id, moment in seen.items()
              if starts is not None and moment >= starts
              and oldest <= moment <= now - window]
    converted = sum(1 for chat_id in cohort
                    if chat_id in regs and regs[chat_id] - seen[chat_id] <= window)

    return ActivityReport(
        version=ACTIVITY_VERSION,
        generated_at=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        tz=str(SHOP_TZ),
        days=days,
        log_starts=_shop_day(starts).isoformat() if starts else None,
        users_total=len(seen),
        registered_total=len(regs),
        registered_7d=registered_between(today - timedelta(days=6), today),
        registered_prev_7d=registered_between(today - timedelta(days=13),
                                              today - timedelta(days=7)),
        wau_days=WAU_DAYS,
        wau=wau,
        mau_days=MAU_DAYS,
        mau=mau,
        stickiness=_share(wau, mau),
        activation_days=ACTIVATION_DAYS,
        activation_cohort_days=ACTIVATION_COHORT_DAYS,
        activation_cohort=len(cohort),
        activation_registered=converted,
        activation=_share(converted, len(cohort)),
        daily=daily,
        weekly=weekly,
    )
