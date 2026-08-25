"""What a scenario needs from the queue every proactive message passes through.

Six scenarios import `core.repos.outbox` directly — five to put a message in,
one to take messages out — and that import is a large part of what keeps
`core.usecases` outside `core-siblings-are-independent`.

**Why two protocols over one table.** The five senders never claim, never shelve
and never ask what a campaign came to; the drainer never queues anything. One
protocol would make the fake in every sweep test implement five methods it does
not call, and — the part that outlives the tests — it would stop saying which
side of the queue a module is on. §6.6 moves senders onto the outbox one at a
time: `MessageQueue` is what they are moved onto, and `PendingMessages` is what
moves to a worker process on the day §4.2's one-sender rule stops being a
comment and becomes `SKIP LOCKED`.

**Why neither hangs off the UnitOfWork.** For `PendingMessages` it is §6.3:
claim in one short statement, commit, send outside the transaction, because
holding one across a thirty-second `retry_after` is how a queue becomes a pile
of stuck locks. For `MessageQueue` it is honesty about today — queueing opens
its own connection and commits on its own, so a unit could not have included it
even if it were declared. Two scenarios already pay for that: referrals writes
the reward row before queueing, stock queues before clearing subscriptions, each
with a comment about the crash in between and each papered over with a dedup
key. On Postgres the queue write belongs in the caller's transaction and this
becomes `uow.messages`; the dedup keys survive that change, so nothing here has
to be got right twice.

**One implementation, deliberately.** `outbox` is not among the twelve tables in
Alembic revision 001, so there is no Postgres side to keep honest yet and the
usual argument for a port — that two implementations cannot drift — does not
apply here today. What it buys instead is the seam: the scenarios stop naming a
table, and the second implementation arrives without touching them. Do not read
the Protocol as proof of portability until revision 002 exists.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Protocol, runtime_checkable

from core.domain.campaign import CampaignKey
from core.domain.delivery import CampaignTotals, OnUncertain, QueuedMessage


@runtime_checkable
class MessageQueue(Protocol):
    """Somewhere a message the bot sends on its own initiative can be put.

    Everything the bot says without being spoken to goes through here — §6.1's
    single place where all of it can be seen. Nothing on this protocol sends
    anything, and that separation is the feature: a scenario that queues cannot
    be slow, cannot be rate limited, and cannot half-finish.
    """

    async def queue(
        self,
        chat_id: int,
        kind: str,
        campaign: CampaignKey,
        payload: dict,
        *,
        dedup_key: str | None = None,
        on_uncertain: OnUncertain = OnUncertain.RETRY,
        respect_quiet: bool = True,
    ) -> int | None:
        """Queue one message. Returns its id, or None if it was already queued.

        `campaign` is required and typed per §6.4: a proactive message without
        one is a send that can be counted but not attributed, and attribution
        cannot be added afterwards because the button is already built.

        **None is an answer, not a failure.** The birthday sweep runs hourly and
        the restock sweep every fifteen minutes; both re-queue what they queued
        last time and rely on `dedup_key` to write nothing the second time. Two
        callers count the Nones — that is what "queued 3 of 50 celebrating
        chats" means — so this cannot raise instead, and it cannot return the
        existing id either: the caller would count it as new.

        `dedup_key` makes a *job* safe to run twice. It does not make sending
        safe to repeat; nothing does (§6.1).

        `respect_quiet=False` is for messages that are not the bot's own idea —
        a manager answering a person who is waiting. The flag is stored rather
        than applied here, because quiet hours are decided at send time against
        the clock then, not against the clock at queueing.
        """
        ...

    async def queue_all(
        self,
        chat_ids: list[int],
        kind: str,
        campaign: CampaignKey,
        payload: dict,
        *,
        dedup_prefix: str | None = None,
        on_uncertain: OnUncertain = OnUncertain.RETRY,
    ) -> int:
        """Queue the same message for many people. Returns how many were new.

        Separate from `queue` because the broadcast path is the one place where
        the loop is somebody's problem: twenty thousand calls would be twenty
        thousand connections while an admin watches a spinner. A caller could
        write that loop and be correct; it could not make it one statement, and
        only the implementation knows whether it can be.

        The list is the snapshot — whoever is in it now is who this campaign is
        responsible for, and an opt-out afterwards does not pull a queued
        message back out. That is what the job/target tables did before the
        outbox, kept deliberately.

        `dedup_prefix` rather than a key per recipient: the caller has one
        campaign and one reason, and per-recipient keys derived at the call site
        are the same string written twice in two files.
        """
        ...

    async def campaign_totals(self, campaign: CampaignKey) -> CampaignTotals:
        """How one campaign is doing: sent, blocked, failed, still waiting.

        On the queueing side rather than the draining side because the scenario
        that asks is the one that started the campaign — it decides a broadcast
        is over when nothing of it is waiting, and then queues the summary
        through `queue` above. The sender never asks.
        """
        ...


@runtime_checkable
class PendingMessages(Protocol):
    """The messages waiting to go out, and the four ways one can be resolved.

    One process at a time. §4.2 and the price table in
    docs/postgres-migration.md rest on that; capture is only safe under it on
    SQLite, and that is pinned by a test rather than left as a comment here.
    """

    async def take_due(
        self, limit: int = 50, *, now: datetime | None = None
    ) -> list[QueuedMessage]:
        """Take up to `limit` messages whose turn has come, and hold them.

        Holding is the whole point — the caller is about to spend seconds in
        HTTP calls, and a second pass starting meanwhile must not send the same
        message again. The hold expires on its own, because a process that dies
        mid-send releases nothing.

        **The attempt is counted at capture, not on failure.** A message that
        kills the process never fails in any way the code can see, so counting
        failures would let it be picked up forever; counting captures means it
        reaches the limit and lands on the shelf instead. The caller reads
        `attempts` and decides — this protocol enforces no maximum, because how
        many tries is too many is the sender's policy and belongs next to its
        backoff table.

        Order is by when a message became due, so a retry pushed into the future
        waits its turn instead of jumping the queue by having been created
        earlier.

        `now` is injectable for the same reason it is on `retry_later`: capture
        and reschedule must be able to share one clock, or a test passes for the
        wrong reason.
        """
        ...

    async def left_in_doubt(
        self, *, now: datetime | None = None
    ) -> list[QueuedMessage]:
        """Messages a crash left unresolved whose policy refuses to guess (§6.2).

        A hold that expired without anybody releasing it is the signature of a
        process that died: nobody can say whether the message went out. Rows
        whose policy is RETRY are simply due again and come back from
        `take_due`; these are the REVIEW ones, and nothing sends them. They
        exist to be shelved and shown to a person, because the alternative is a
        broadcast that re-sends itself after every restart.

        Separate from `take_due` rather than a flag on it, because the two lists
        have opposite fates and a caller that confused them would send the one
        that must never be sent.
        """
        ...

    async def mark_sent(self, message_id: int) -> None:
        """It went. The narrow window §6.1 admits to closes here."""
        ...

    async def retry_later(
        self,
        message_id: int,
        error: str,
        *,
        wait: timedelta,
        now: datetime | None = None,
    ) -> None:
        """It failed and we know it did, so this is not the uncertain case.

        Releasing the hold is what says so: a message released this way is
        claimable again whatever its policy, because there is nothing left to be
        unsure about. That is the difference between this and dying mid-send,
        and it is the only thing that keeps `left_in_doubt` meaning what it
        says.

        `wait` comes from the caller because only the caller knows which kind of
        failure it was — a 429 names its own wait, anything else gets the
        backoff table. `now` is injectable because this write decides when the
        message becomes due again, and a schedule computed from a different
        clock than the one that reads it is a bug no test would catch.
        """
        ...

    async def shelve(
        self, message_id: int, reason: str, *, recipient_gone: bool = False
    ) -> None:
        """Out of the queue, still on the record.

        Deleting would be tidier and would erase the evidence; a shelved message
        is what somebody reads when a customer says they never got it.

        `recipient_gone` separates "this person left" from "this send broke",
        and it is a parameter rather than a phrasing of `reason` because
        `campaign_totals` has to count the two apart — they are opposite news to
        whoever reads the summary. Today the implementation encodes it in the
        stored text and matches on a prefix; a column would be better and is not
        this port's business, which is exactly why the caller states the fact
        instead of spelling the prefix.
        """
        ...
