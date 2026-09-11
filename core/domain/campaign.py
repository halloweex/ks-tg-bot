"""What a proactive message was sent for, in a form that survives being stored.

§6.4 asked for a campaign key that is mandatory when queueing a message *and*
present in the `callback_data` of the button on it, so that the outbox header
would record what was sent, the callback would record what was pressed, and
`events` would join them into sent → clicked → bought.

**Half of that was never buildable, and saying so here is the point of this
paragraph.** No proactive message this bot sends carries a callback button:
three of the six kinds carry no keyboard at all, two carry a `url` button —
Telegram tells a bot nothing when one is tapped — and the sixth opens the inline
panel with a query the customer can see in her own input field. There was
nothing to stamp the key into, which is why `parse()` below sat with no callers
for as long as it did. The middle of that funnel can only be bought by putting a
button on a message that has none, aimed back into the bot rather than at the
shop, and that is a product change made to obtain a number.

So the key does the half it can, and it does it well: written once onto the
outbox row, kept there for a year — `prune()` clears the payload and leaves the
header — and read by `core/usecases/analytics.py::campaign_report`, which joins
it to the orders the CRM sync records without the customer touching the bot.
Sent → bought, honestly, with the click left out rather than inferred.

**Why a type and not a string.** The same value is produced in the job that
queues and grouped in the report that reads. Two places, two chances to spell it
differently, and a typo is invisible: the campaign simply splits into two rows
that each look like a smaller campaign. A parsed type turns that into an error
at the boundary — which is also why `parse` raises rather than shrugging.

**Why `.` separates the parts.** It was chosen because aiogram packs
`callback_data` with `:`, and a value containing one is rejected or silently
split. That reason is now historical, since the key never reaches a
`callback_data`; the separator stays because keys are already written in the
outbox with it and a change would make every stored row unreadable for nothing.
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
    """A key back out of a database row.

    It said "out of a callback or a database row" and only the second half was
    ever reachable — see the note at the top of this file. Its one caller is
    the campaign report, which reads the keys the senders wrote.

    Raises on anything that is not one, rather than returning a blank: a
    malformed key means a message built by code that no longer exists, and
    counting it as an unnamed campaign would quietly corrupt every funnel it
    lands in.
    """
    kind, _, ref = raw.partition(".")
    if not ref:
        raise ValueError(f"not a campaign key: {raw!r}")
    return CampaignKey(kind, ref)
