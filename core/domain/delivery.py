"""The facts of a proactive send, named where both ends can read them.

Everything the bot says without being spoken to goes through one queue (§6.1),
and the scenario that puts a message in and the table that keeps it have to
agree about three things: what to do when a send is left in doubt, what a
queued message *is*, and what a finished campaign amounts to. All three lived
in `core/repos/outbox.py` — a module name, which is to say a table — and the
scenarios imported them from there.

They belong here instead. Not because layering says so, but because both ends
must read them and `core.repos` importing `core.ports` is a dependency the
target structure does not want. Domain is the floor both stand on.

Named `delivery` rather than `outbox`: the outbox is a table, and a domain
named after a repository is a repository with extra steps.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class OnUncertain(StrEnum):
    """What to do with a message whose fate a restart left unknown (§6.2).

    A sender that dies between `send` and `mark_sent` leaves a row that was
    perhaps delivered. There is no way to find out, so the decision is made in
    advance and per message, because the right answer is not the same twice:

    - `RETRY` — a missed delivery status is worse than a duplicate one. Send
      it again and accept that somebody may read "your parcel arrived" twice.
    - `REVIEW` — a duplicated broadcast is worse than a missed one. Park it and
      let a person decide, because twenty thousand people receiving the same
      offer twice is not a glitch, it is the shop looking broken.

    A `StrEnum`, so the stored value stays byte-identical to the string the
    column already holds. No migration, and an old row keeps meaning what it
    meant.
    """

    RETRY = "retry"
    REVIEW = "review"


@dataclass(frozen=True)
class QueuedMessage:
    """One message waiting to go out, as the sender needs to see it.

    Replaces the raw cursor row the sender used to index by column name. That
    was the schema of one table serving as the interface of the scenario that
    reads it: `row["type"]`, `row["respect_quiet"]`, `row["payload"]`. It also
    pinned any second implementation to SQLite's spelling and typing — a
    `respect_quiet` that is 0 or 1 rather than a bool, a `payload` that is JSON
    text where `jsonb` hands back a dict.

    `kind` is the field; `type` stays the column. Renaming a column would be a
    migration, and naming a new type well is not one.
    """

    id: int
    kind: str
    chat_id: int
    payload: dict
    campaign_key: str
    attempts: int
    last_error: str | None
    #: Whether quiet hours apply. Stored rather than applied at queueing time,
    #: because quiet hours are decided against the clock at *send* time — a
    #: message queued at noon may go out at midnight.
    respect_quiet: bool


@dataclass(frozen=True)
class CampaignTotals:
    """What one campaign came to, for the person who asked for it.

    Four numbers instead of a dict with four magic keys, where a typo in any of
    them was a KeyError discovered at the moment a broadcast finished.

    **`blocked` is not `failed`.** One is people who are gone — blocked the bot,
    deleted the account — and the other is sends that broke. To whoever reads
    the summary they mean opposite things: the first is the shop's audience
    shrinking, the second is the shop's machinery. The distinction predates the
    outbox and survives it.
    """

    waiting: int
    sent: int
    failed: int
    blocked: int
