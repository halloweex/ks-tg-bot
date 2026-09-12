"""Reading `app.buyer_gender` out of the warehouse's Postgres.

The one store in this repository that belongs to somebody else. The neighbouring
project (`/opt/key-api-bot`) owns the database `ks`, writes the table as `ks_app`
and grants SELECT on the `app` schema to `ks_readonly`; this reads it as that
role and never writes anything. It lives in `core.repos` because that is where
the driver is allowed to be — `only-repos-touch-db` names asyncpg — and not
because the table is ours.

**Why the table and not the column.** `bronze.buyers` is reconciled against
DuckDB with zero tolerance over a fixed tuple of columns, so a gender column
there would either be invisible to the check or compared against a value KeyCRM
does not have. `app.customer_profile` is TRUNCATE'd and rebuilt every two
minutes by the warehouse tick, so a human's correction would survive about
ninety seconds. A table of its own is the only place a decision can be both
joined and kept.

**A connection per call, no pool.** Two callers ask: registration, once per new
customer, and the refresh sweep, once an hour for everybody. Neither is hot, and
the neighbour's Postgres runs with `max_connections=40` for a whole platform —
an idle pool held by this bot would be capacity taken from the service that owns
the database. The cost is a handshake on a docker network, which is cheaper than
the query it carries.

**No `ORDER BY`, no `LIMIT`, no filtering on confidence.** The bot asks about the
buyer cards one chat turned out to be, which is one row in almost every case and
never more than a handful; and the classifier already expressed its doubt by
writing NULL. Re-gating on `confidence` here would be a threshold nobody
measured applied to somebody else's measurement — see core/domain/gender.py.
"""
from __future__ import annotations

import asyncpg
from loguru import logger

#: Seconds. The query is an indexed lookup of a few ids in a 20k-row table, so
#: anything near this means the other database is in trouble — and the caller's
#: reaction to a timeout and to an error is the same one: change nothing.
TIMEOUT = 5.0

_QUERY = """
SELECT buyer_id, gender
  FROM app.buyer_gender
 WHERE buyer_id = ANY($1::int[])
"""


class PgBuyerGenders:
    """Implements core.ports.gender.BuyerGenders against the warehouse."""

    def __init__(self, dsn: str, *, timeout: float = TIMEOUT) -> None:
        self._dsn = dsn
        self._timeout = timeout

    async def for_buyers(self, buyer_ids: set[str]) -> dict[str, str | None]:
        """The rows for these buyers, keyed as the caller spells them.

        Non-numeric ids are dropped before the query rather than after: the
        column is an integer, and one unparseable id in the set would otherwise
        cost the whole lookup with a cast error. It has not happened — KeyCRM
        client ids are numbers — and a set that turns out to be entirely
        unparseable is answered with nothing rather than with a query.
        """
        numeric = {}
        for raw in buyer_ids:
            try:
                numeric[int(str(raw).strip())] = str(raw)
            except (TypeError, ValueError):
                logger.debug("Skipping non-numeric buyer id {!r}", raw)
        if not numeric:
            return {}

        connection = await asyncpg.connect(self._dsn, timeout=self._timeout)
        try:
            rows = await connection.fetch(_QUERY, list(numeric),
                                          timeout=self._timeout)
        finally:
            await connection.close()

        # Keyed back to the caller's spelling, so a chat's cards and the answer
        # about them are comparable without anybody casting twice.
        return {numeric[row["buyer_id"]]: row["gender"] for row in rows
                if row["buyer_id"] in numeric}
