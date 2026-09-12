"""Where the decision about a buyer's gender comes from.

One method, because there is one question: for these CRM buyer cards, what does
the table say? Everything else about it — the classifier, its rules version, the
confidence it recorded, the overrides a human typed — happens outside this
repository and arrives already collapsed into the value.

**Why a port at all for a single read.** The store is not this bot's: the table
lives in the warehouse the neighbouring project writes (`app.buyer_gender` in
Postgres, `buyer_gender` in DuckDB). Reaching it means a second database, a
credential and a network; a scenario that imported the reader directly could not
be tested without all three, and — the part that matters longer — the bot's own
move to Postgres will turn that reader into a join in the same database. The
scenario should not notice the day that happens.

**Absent is a legal implementation.** Nothing is configured until somebody puts
a DSN in the environment, and until then the sweep does not run and every chat
keeps the form it has. That is why callers take `BuyerGenders | None` rather than
a null object: "there is no source" is a state worth seeing in the logs, not a
source that answers "unknown" twenty thousand times.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class BuyerGenders(Protocol):
    """The warehouse's answer for a set of CRM buyer cards."""

    async def for_buyers(self, buyer_ids: set[str]) -> dict[str, str | None]:
        """What the table holds for each of these buyers.

        Keyed the way the bot spells a buyer id — a string, as `user_crm_buyers`
        stores it and as KeyCRM hands it over. A buyer with no row at all is
        simply absent from the result; a buyer whose row says NULL is present
        with None, because the two mean different things and only the second one
        is the classifier having looked.

        Raising is allowed and expected: the other database can be down, and the
        caller's answer to that is to leave every form exactly as it is.
        """
        ...
