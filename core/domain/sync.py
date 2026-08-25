"""How far the sync has read, and how a moment is spelled on the way to storage.

Two things that both ends need and neither owns. The sweep decides which window
to read next from what it last recorded; the repository writes that record and
reads it back. Between them sits one timestamp format, and the whole reason it
is here is that a format spelled in two places is a format that eventually
disagrees with itself — which for this table means a cursor that reads as
missing and a window that silently starts a month late.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

# Both the CRM filter and SQLite's datetime() speak this, in UTC. It is not a
# display format and nothing renders it to a customer: it is the spelling the
# `sync_state` columns and the changed-orders request happen to share, which is
# the only reason one constant can serve both.
STAMP = "%Y-%m-%d %H:%M:%S"


def write_stamp(moment: datetime) -> str:
    """A moment as the column stores it."""
    return moment.strftime(STAMP)


def read_stamp(stamp: str | None) -> datetime | None:
    """A timestamp as stored, or None if it is missing or unreadable.

    Unreadable is treated as missing rather than raised on, and the direction of
    that choice is deliberate: callers use it to decide how far back to read,
    and the safe answer to "I cannot tell" is the wider window. Erring the other
    way — treating an unparseable cursor as recent — is how a sweep skips the
    orders it could not place a boundary around, permanently, because the window
    that held them is behind the cursor forever.
    """
    if not stamp:
        return None
    try:
        return datetime.strptime(stamp, STAMP).replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True, slots=True)
class SyncState:
    """One integration's row, as the scenario and the watchdog read it.

    Times, not strings. The parsing belongs to whoever stored them — under
    SQLite these are TEXT columns and under Postgres they would be timestamptz,
    and a scenario that had to know which is a scenario tied to one engine.

    **Every field is optional and each None means something different**, which
    is why they are separate columns rather than one "status":

    * `cursor` — the upper bound of the last window read to the end. None means
      never swept, and the sweep reads the reconciliation window instead of
      starting from now, because a cursor beginning at "now" leaves the orders
      of everyone who registered earlier permanently unfetched.
    * `last_run_at` — written when a sweep *starts*. Separates "sweeps are
      happening and failing" from "nothing is running at all"; the two have
      different causes and different fixes.
    * `last_success_at` — what the §5.5 alert reads, rather than `last_error`,
      because the failures that matter raise nothing at all: a cancelled task,
      a loop that stopped being scheduled. Monitoring that waits for an error
      sees a healthy system whose data froze hours ago.
    * `last_error` — an operational message for a person, truncated by the
      implementation and never trusted to be short.
    * `last_full_at` — when the weekly reconciliation last finished, so an
      incremental sweep cannot postpone it forever.
    """

    source: str
    cursor: datetime | None = None
    last_run_at: datetime | None = None
    last_success_at: datetime | None = None
    last_error: str | None = None
    last_full_at: datetime | None = None
