"""What a proactive message was sent for, in a form that survives the round trip.

§6.4 asks for a campaign key that is mandatory when queueing a message *and*
present in the `callback_data` of the button on it. That pairing is the whole
point: the outbox header records what was sent, the callback records what was
pressed, and `events` joins them into sent → clicked → bought. Without the key
in both places the funnel has a hole in the middle and only ever answers "sent"
and "bought", which is the question nobody is stuck on.

It cannot be bolted on afterwards — a message already delivered carries whatever
callback_data it was built with — so the type exists before the first sender
moves onto the outbox rather than after.

**Why a type and not a string.** The same value has to be produced in the job
that queues, matched in the handler that receives, and grouped in the query that
reports. Three places, three chances to spell it differently, and a typo is
invisible: the funnel simply reports fewer clicks. A parsed type turns that into
an error at the boundary.

**Why `.` separates the parts.** aiogram packs `callback_data` with `:`, and a
value containing one is rejected or silently split — so the separator here is
the one character that cannot be used there.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Final

# Telegram allows 64 bytes for the whole of callback_data, and the key is only
# one field in it — next to a prefix, an action and sometimes a sku. Kept short
# enough that the rest of the payload is not squeezed by it.
MAX_LEN: Final = 17

_KIND: Final = re.compile(r"^[a-z][a-z0-9_]{1,7}$")
_REF: Final = re.compile(r"^[a-z0-9]{1,8}$")


@dataclass(frozen=True)
class CampaignKey:
    """One reason messages went out, and one bucket to count the answers in.

    `kind` is what the message is — a restock, a broadcast, a replenishment
    reminder. `ref` is which run of it: a day for anything recurring, an id for
    anything that happens once.
    """

    kind: str
    ref: str

    def __post_init__(self) -> None:
        if not _KIND.match(self.kind):
            raise ValueError(
                f"campaign kind must be 2-8 chars of [a-z0-9_] starting with a "
                f"letter, got {self.kind!r}"
            )
        if not _REF.match(self.ref):
            raise ValueError(
                f"campaign ref must be 1-8 chars of [a-z0-9], got {self.ref!r}"
            )
        if len(str(self)) > MAX_LEN:
            raise ValueError(f"campaign key {self!s} is longer than {MAX_LEN} chars")

    def __str__(self) -> str:
        return f"{self.kind}.{self.ref}"


def daily(kind: str, day: date | None = None) -> CampaignKey:
    """The key for a job that runs on a schedule: one bucket per day.

    A day rather than a per-run id because a restock sweep runs every fifteen
    minutes, and counting each sweep separately would give ninety-six campaigns
    a day that nobody wants to read. What a report asks is "how did Tuesday's
    restock notifications do".
    """
    day = day or date.today()
    return CampaignKey(kind, day.strftime("%y%m%d"))


def parse(raw: str) -> CampaignKey:
    """A key back out of a callback or a database row.

    Raises on anything that is not one, rather than returning a blank: a
    malformed key means a message built by code that no longer exists, and
    counting it as an unnamed campaign would quietly corrupt every funnel it
    lands in.
    """
    kind, _, ref = raw.partition(".")
    if not ref:
        raise ValueError(f"not a campaign key: {raw!r}")
    return CampaignKey(kind, ref)
