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
    """Replace what we know about these skus.

    An empty sweep writes nothing: the adapter returns {} for a failed read, and
    taking that literally would mark the whole catalogue unbuyable.
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
