"""The storefront's offers, as last read from its public feed.

A cache and nothing more: the shop is the authority, this table is what we can
answer from without waiting on it. Written whole by the sweep, read a handful of
skus at a time by the screens that offer to buy something.
"""
from __future__ import annotations

from collections.abc import Iterable

from core.domain.offer import Offer
from core.repos.base import connect


def _row_to_offer(row) -> Offer:
    return Offer(
        sku=row[0],
        variant_id=row[1],
        handle=row[2],
        title=row[3],
        price=row[4],
        available=bool(row[5]),
        image_url=row[6],
    )


async def save_offers(offers: dict[str, Offer]) -> None:
    """Update what we know about these skus, leaving every other row standing.

    An empty sweep writes nothing: the adapter returns {} for a failed read, and
    taking that literally would mark the whole catalogue unbuyable.

    This is the per-sku write and stays one. The hourly sweep wants
    `replace_offers` below, which also removes what the shop has stopped
    listing; the two are separate functions rather than a flag because a caller
    holding five skus must never be one keyword away from emptying the table.
    """
    if not offers:
        return
    async with connect() as db:
        await db.executemany(
            "INSERT INTO offers (sku, variant_id, handle, title, price, available, "
            "                    image_url, checked_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now')) "
            "ON CONFLICT(sku) DO UPDATE SET variant_id = excluded.variant_id, "
            "                               handle     = excluded.handle, "
            "                               title      = excluded.title, "
            "                               price      = excluded.price, "
            "                               available  = excluded.available, "
            "                               image_url  = excluded.image_url, "
            "                               checked_at = excluded.checked_at",
            [(o.sku, o.variant_id, o.handle, o.title, o.price, int(o.available),
              o.image_url)
             for o in offers.values()],
        )
        await db.commit()


# A row is deleted only after the sweeps have stopped seeing it for this long.
# The watcher runs hourly (`bot/catalogue.py`), so this has to sit strictly
# BETWEEN one interval and two: above 60 minutes so a row missed by one sweep
# survives, below 120 so a row missed by two goes. Ninety is the middle, which
# leaves half an hour of scheduling slack on either side.
#
# It was two hours first, and that was wrong in a way worth recording: at
# exactly two intervals the row is exactly two hours old, the comparison is
# strict, and deletion slipped to the third sweep. One hour later than intended
# is harmless; a constant that does not do what the comment above it says is
# not, and this file has spent the day proving that.
#
# **One observation is not enough to delete on**, because the thing being
# observed is a paginated feed that can be wrong in ways nothing downstream can
# detect. Two have already been found: a read that stopped at the page cap, and
# a page answering 200 with an empty list. A third needs no fault at all —
# `/products.json` pages by offset and recomputes the slice per request, so a
# product unpublished between two page reads shifts the window and the product
# on the boundary is returned by no page. It was present, it is absent, and
# nothing distinguishes that from delisted. Measured against the live feed:
# 638 products over three pages, ~2-3s between boundary reads, and the row that
# gets deleted is the wrong one — the count is right and the identity is not.
#
# Waiting a second sweep costs a delisted product one extra hour of a buy
# button. Not waiting costs a listed one its buy button on a coin flip nobody
# can see. `checked_at` has been written on every sweep since the table existed
# and read by nothing; this is what it was for.
_MISSED_SWEEPS_BEFORE_DELETING = "-90 minutes"


async def replace_offers(offers: dict[str, Offer]) -> int:
    """Make the table say exactly this, and return how many rows it removed.

    Implements `OfferCache.replace`, whose docstring carries the reasoning: a
    non-empty mapping is the entire catalogue, so a sku that is not in it is one
    the storefront has stopped listing, and a row that outlives the listing goes
    on offering «Купити» at the last price it ever had.

    The surviving skus go into a temporary table rather than into an `IN (?, ?,
    …)` list. Six hundred parameters works today and the page cap allows five
    thousand, which is past SQLite's default limit — and the failure mode of
    finding that out in production is an exception in the sweep, or worse, a
    delete that ran with a truncated list.

    Write first, delete second, one transaction: the two halves are one
    statement about what the shop sells, and a reader between them would see a
    catalogue that never existed.

    Absence is necessary and not sufficient: a row goes only when the sweeps
    have also stopped touching it for `_MISSED_SWEEPS_BEFORE_DELETING`. The note
    on that constant says why, and it is the reason `checked_at` exists.
    """
    if not offers:
        # The one line standing between a failed read and an empty shop.
        return 0
    async with connect() as db:
        await db.execute("CREATE TEMP TABLE IF NOT EXISTS listed (sku TEXT PRIMARY KEY)")
        await db.execute("DELETE FROM listed")
        await db.executemany("INSERT OR IGNORE INTO listed (sku) VALUES (?)",
                             [(o.sku,) for o in offers.values()])
        await db.executemany(
            "INSERT INTO offers (sku, variant_id, handle, title, price, available, "
            "                    image_url, checked_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now')) "
            "ON CONFLICT(sku) DO UPDATE SET variant_id = excluded.variant_id, "
            "                               handle     = excluded.handle, "
            "                               title      = excluded.title, "
            "                               price      = excluded.price, "
            "                               available  = excluded.available, "
            "                               image_url  = excluded.image_url, "
            "                               checked_at = excluded.checked_at",
            [(o.sku, o.variant_id, o.handle, o.title, o.price, int(o.available),
              o.image_url)
             for o in offers.values()],
        )
        cursor = await db.execute(
            "DELETE FROM offers "
            " WHERE sku NOT IN (SELECT sku FROM listed) "
            f"   AND checked_at < datetime('now', '{_MISSED_SWEEPS_BEFORE_DELETING}')")
        removed = cursor.rowcount or 0
        await db.commit()
    return removed


async def get_offers(skus: Iterable[str]) -> dict[str, Offer]:
    """The offers for these skus, missing ones simply absent.

    Asked for by sku rather than read whole because every caller is a screen
    holding five products, and the table holds six hundred.
    """
    wanted = [s for s in {str(s).strip() for s in skus} if s]
    if not wanted:
        return {}
    placeholders = ",".join("?" * len(wanted))
    async with connect() as db:
        cursor = await db.execute(
            "SELECT sku, variant_id, handle, title, price, available, image_url "
            f"FROM offers WHERE sku IN ({placeholders})",
            wanted,
        )
        return {row[0]: _row_to_offer(row) for row in await cursor.fetchall()}


async def count_offers() -> int:
    """How many offers are cached — for the sweep's log line and its tests."""
    async with connect() as db:
        cursor = await db.execute("SELECT COUNT(*) FROM offers")
        return (await cursor.fetchone())[0]


class SqliteOfferCache:
    """Implements core.ports.repositories.OfferCache against today's database.

    One method over `replace_offers`, which keeps its own callers and its own
    tests. The seam moves; the query does not.

    `get_offers` deliberately stays a plain function: its callers are screens,
    and screens still import repositories. It gets a read port of its own on the
    day the handlers-see-ports-only contract is uncommented, driven by the
    screen that asks rather than by the column that exists.
    """

    async def replace(self, offers: dict[str, Offer]) -> int:
        return await replace_offers(offers)

    async def update(self, offers: dict[str, Offer]) -> None:
        await save_offers(offers)

    async def count(self) -> int:
        return await count_offers()
