"""The orders cache: one row per physical order per chat.

Identity is decided in core/domain/order.py and applied here — merge_key and
source_rank are derived on the way in, never accepted from a caller.
"""
from __future__ import annotations

import aiosqlite

from core.domain.order import merge_key, source_rank
from core.repos.base import connect


_ORDER_COLUMNS = (
    "chat_id", "merge_key", "source", "source_order_id", "source_rank",
    "external_id", "order_name",
    "status_name", "status_group_id", "grand_total", "currency", "ordered_at",
    "products_json",
    "buyer_name", "payment_status", "tracking_code", "shipping_status",
    "delivery_city", "receive_point", "recipient_name",
)

# On conflict, whose value wins.
#   _OVERWRITE — the order as the CRM currently reports it. The newer read wins,
#                full stop. This used to be conditional on source_rank, so that
#                a Shopify copy could not overwrite KeyCRM's; §4.4 removed the
#                branch with the second writer it existed for. While one source
#                writes, the two forms are identical — an incoming rank always
#                equals the stored one, so the condition was always true.
#   _KEEP_BEST — a value a read may not carry; a non-empty incoming value fills
#                a gap, an empty one never erases. Still exercised every sweep:
#                an Instagram order has no external_id and no order_name.
_OVERWRITE = ("source", "source_order_id", "status_name", "status_group_id",
              "grand_total", "currency", "ordered_at", "products_json",
              "buyer_name", "payment_status", "tracking_code", "shipping_status",
              "delivery_city", "receive_point", "recipient_name")
_KEEP_BEST = ("external_id", "order_name")

# Seeded by the /demo admin command, never by a sync. Kept out of the real
# sources so it can be deleted precisely and can never collide with a genuine
# order id.
DEMO_SOURCE = "demo"

# KeyCRM's cancelled / returned / out-of-stock status family. An order in this
# group is not in transit, so it must not appear under delivery tracking as if
# it were on its way.
CANCELLED_STATUS_GROUP = 6

def _build_upsert() -> str:
    """INSERT ... ON CONFLICT, assembled from the column policy above.

    Written out rather than hand-typed because it is twenty near-identical
    assignments and a single wrong column name would silently stop one field
    from ever updating.

    It used to carry a CASE WHEN per column, comparing source_rank so that the
    junior source could not overwrite the senior one. §4.4 asked for that to go
    once Shopify stopped writing, and the reason is worth keeping: conditional
    code nothing exercises is worse than deleted code, because in a year it is
    needed and does not work. The column stays as a mark of provenance, and the
    branch comes back with the second writer and with its test.
    """
    cols = ", ".join(_ORDER_COLUMNS)
    placeholders = ", ".join("?" for _ in _ORDER_COLUMNS)
    sets = [f"{c} = excluded.{c}" for c in _OVERWRITE]
    sets += [f"{c} = CASE WHEN excluded.{c} != '' THEN excluded.{c} ELSE orders.{c} END"
             for c in _KEEP_BEST]
    sets.append("source_rank = excluded.source_rank")
    sets.append("synced_at = datetime('now')")
    return (
        f"INSERT INTO orders ({cols}, synced_at) "
        f"VALUES ({placeholders}, datetime('now')) "
        # The WHERE repeats the partial index's predicate: SQLite refuses the
        # conflict target otherwise ("does not match any PRIMARY KEY or UNIQUE
        # constraint"), and PostgreSQL needs the same to pick a partial index.
        f"ON CONFLICT(chat_id, merge_key) WHERE merge_key != '' "
        f"DO UPDATE SET " + ", ".join(sets)
    )


_UPSERT_SQL = _build_upsert()


async def upsert_orders(chat_id: int, orders: list[dict]) -> None:
    """Bulk upsert orders from API results into the local cache.

    Each dict must have keys matching _ORDER_COLUMNS. Rows are keyed by
    merge_key (bot/merge.py), so the same physical order reported by both
    systems lands on one row whichever arrives first, and the more
    authoritative source's values are the ones kept.

    This replaced a delete-the-Shopify-copy sweep that ran after every refresh.
    A unique index does the job once, in the schema, and also refuses to write
    the duplicate rather than cleaning it up afterwards.
    """
    if not orders:
        return
    async with connect() as db:
        for order in orders:
            row = dict(order)
            # Derived here and nowhere else. Letting callers pass merge_key
            # would mean a caller that forgets it writes an empty one, and an
            # empty key is shared by every such row — the unique index would
            # then collapse unrelated orders into one. Found exactly that way.
            row["merge_key"] = merge_key(
                row.get("source", ""), str(row.get("source_order_id", "")),
                row.get("external_id") or None,
            )
            row["source_rank"] = source_rank(row.get("source", ""))
            values = tuple(row.get(col, "") for col in _ORDER_COLUMNS)
            await db.execute(_UPSERT_SQL, values)
        await db.commit()


async def get_cached_orders(chat_id: int) -> list[dict]:
    """Return all cached orders for a user, newest first."""
    async with connect() as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM orders WHERE chat_id = ? ORDER BY ordered_at DESC",
            (chat_id,),
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


async def get_orders_with_tracking(chat_id: int) -> list[dict]:
    """Cached orders with a tracking code that are still worth tracking.

    Cancelled and returned orders keep their tracking code, so without the
    status-group filter they showed up under delivery as active shipments.
    """
    async with connect() as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM orders WHERE chat_id = ? AND tracking_code != '' "
            "AND COALESCE(status_group_id, 0) != ? "
            "ORDER BY ordered_at DESC",
            (chat_id, CANCELLED_STATUS_GROUP),
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


async def get_last_sync_time(chat_id: int) -> str | None:
    """Return the most recent synced_at timestamp for a user's orders, or None."""
    async with connect() as db:
        cursor = await db.execute(
            "SELECT MAX(synced_at) FROM orders WHERE chat_id = ?",
            (chat_id,),
        )
        row = await cursor.fetchone()
        return row[0] if row and row[0] else None


async def delete_demo_orders(chat_id: int) -> int:
    """Remove the demo orders seeded for one chat. Returns how many went."""
    async with connect() as db:
        cursor = await db.execute(
            "DELETE FROM orders WHERE chat_id = ? AND source = ?",
            (chat_id, DEMO_SOURCE),
        )
        await db.commit()
        return cursor.rowcount or 0


async def count_demo_orders(chat_id: int) -> int:
    """How many demo orders are currently seeded for this chat."""
    async with connect() as db:
        cursor = await db.execute(
            "SELECT COUNT(*) FROM orders WHERE chat_id = ? AND source = ?",
            (chat_id, DEMO_SOURCE),
        )
        row = await cursor.fetchone()
        return row[0] if row else 0
