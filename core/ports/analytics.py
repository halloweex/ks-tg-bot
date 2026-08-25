"""What a usage report asks of the event log, expressed without naming a table.

A sibling of `core/ports/repositories.py` rather than a member of it, and the
split is the point.

**Why not a repository on the UnitOfWork.** That port is a transaction because
two writes must land together. Every method here is a read, and none of them
belongs to anybody: hanging them on the unit would give every registration
transaction a repository it never touches, and would make the admin readout —
which has no user and wants no per-user filtering — reach for `user_id=None`
just to count rows that were never per-user in the first place.

**Why the write is not here.** `log_event` has exactly one caller,
`bot/analytics.py`, and it is not a scenario: `track()` spawns it as a
background task so a slow disk cannot stall a screen. Porting that write means
deciding whether an analytics append may share a scenario's transaction, and
today it must not — the task outlives the block that spawned it. This port does
not pre-answer that question; the write gets its own one-method port next to
this one on the day handlers stop importing repositories.
"""
from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable


@runtime_checkable
class UsageStats(Protocol):
    """The four questions the usage report asks, and nothing else.

    One port rather than four loose functions, because they are one readout.
    §7 of docs/architecture.md says the same four questions belong to the web
    cabinet and to a weekly digest as much as to /stats, and all four have to
    be answered by the same store over the same clock: a funnel counted in one
    place and a miss rate counted in another produce a report that quietly
    compares two databases.

    **Every window is a required keyword argument.** The module underneath
    carries `days: int = 30` on three functions and `days: int = 7` on the
    fourth, while the report already ships its own windows to the screen
    (`report.days`, `report.event_days`) precisely so the caption and the
    number cannot disagree. A default down here would be a second place where
    the window is decided, and the caller that forgot to pass one is the caller
    whose heading says 30 days over a 7-day count.

    **Known deviation, deliberate.** `days` leaves "now minus N days" to the
    implementation, and the two engines do not agree about `now`: SQLite's is
    UTC, Postgres answers with the server's clock against a `timestamptz`
    column. The disagreement is a few rows at the edge of the window, which is
    to say invisible. Taking a `since: datetime` instead would be honest, but
    the report prints days and would then have to carry both. Pinned by the
    conformance test rather than by the signature.
    """

    async def users_reaching(
        self, steps: Sequence[str], *, days: int
    ) -> dict[str, int]:
        """Distinct users who reached each named step inside the window.

        **The steps are a parameter, not the repository's business.** They were
        written down twice — `FUNNEL_STEPS` in the scenario and a private tuple
        in the SQLite module — and the two were equal only because nobody had
        edited one of them yet. A step added below and not above costs a query
        nobody reads; a step added above and not below prints as a step nobody
        reached, which is indistinguishable from a real collapse in the funnel.
        Which steps make a funnel is the report's decision, so the report says
        it.

        A step nobody reached may be absent from the mapping rather than
        present as zero. The caller reads absent as zero deliberately.
        """
        ...

    async def order_lookup_misses(self, *, days: int) -> tuple[int, int]:
        """(lookups that found nothing, lookups made) in the window, in that order.

        The number that decides whether matching customers by phone works at
        all. A customer who shares a contact and is shown an empty list is the
        worst outcome the whole flow has, and until it was counted it was
        invisible — see docs/found-during-move.md.

        **Only lookups that actually asked the CRM count.** A list served from
        cache says nothing about whether the phone matched, and counting those
        would dilute the rate with repeat taps by one already-happy user. That
        is a rule about which events qualify, not an optimisation, so it lives
        in the contract rather than in whichever query implements it.

        Misses first, total second: the order of the division that follows.
        Both are ints, so the pair is silently transposable, and the order is
        contract with a test pinning it.
        """
        ...

    async def retention(self, *, days: int) -> tuple[int, int]:
        """(users active on more than one day, users active at all), in that order.

        Two counts and not a ratio, because "7 of 30" and "23%" are different
        readouts and the second cannot be recovered from the first. Dividing is
        the report's job — it is also the second place in this scenario where
        the denominator can be zero, and the port must not decide what zero
        active users means.

        Same transposition hazard as the pair above, and it has been paid for
        once already: the test that states 7 of 30 came back, not 30 of 7.
        """
        ...

    async def busiest_events(self, *, days: int) -> list[tuple[str, int, int]]:
        """(event, times it happened, distinct users), busiest first.

        The ordering is part of the contract, not an accident of a query. The
        readout prints this list as it arrives and shows the top of it in a chat
        bubble; an implementation returning insertion order would turn the
        section into a list of whatever happened to be inserted first, and
        nothing in the report would look wrong.

        Occurrences and distinct users are both reported because they answer
        different questions: one screen opened forty times by one person is not
        the same fact as forty people opening it once.
        """
        ...
