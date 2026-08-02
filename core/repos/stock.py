"""Back-in-stock: last known availability, and who is waiting for what.

Two tables. stock_levels is a snapshot the poller compares against, so a restock
can be told from a sku that was simply never out; stock_subscriptions is one row
per person per product, removed once notified — the promise is "tell me when it
is back", not "watch this forever".
"""
from __future__ import annotations

from core.repos.base import connect


async def get_stock_levels() -> dict[str, int]:
    """The last recorded availability per sku."""
    async with connect() as db:
        cursor = await db.execute("SELECT sku, available FROM stock_levels")
        return {row[0]: row[1] for row in await cursor.fetchall()}


async def save_stock_levels(levels: dict[str, int]) -> None:
    """Replace the recorded availability with a fresh snapshot."""
    if not levels:
        return
    async with connect() as db:
        await db.executemany(
            "INSERT INTO stock_levels (sku, available, checked_at) "
            "VALUES (?, ?, datetime('now')) "
            "ON CONFLICT(sku) DO UPDATE SET available = excluded.available, "
            "                               checked_at = excluded.checked_at",
            list(levels.items()),
        )
        await db.commit()


async def add_stock_subscription(chat_id: int, sku: str, name: str) -> None:
    """Register interest in a sku coming back. Idempotent."""
    async with connect() as db:
        await db.execute(
            "INSERT INTO stock_subscriptions (chat_id, sku, name) VALUES (?, ?, ?) "
            "ON CONFLICT(chat_id, sku) DO UPDATE SET name = excluded.name",
            (chat_id, sku, name),
        )
        await db.commit()


async def remove_stock_subscription(chat_id: int, sku: str) -> None:
    async with connect() as db:
        await db.execute(
            "DELETE FROM stock_subscriptions WHERE chat_id = ? AND sku = ?",
            (chat_id, sku),
        )
        await db.commit()


async def get_subscribed_skus(chat_id: int) -> set[str]:
    """Which skus this person is already waiting on."""
    async with connect() as db:
        cursor = await db.execute(
            "SELECT sku FROM stock_subscriptions WHERE chat_id = ?", (chat_id,)
        )
        return {row[0] for row in await cursor.fetchall()}


async def subscribers_for(skus: list[str]) -> list[tuple[int, str, str]]:
    """(chat_id, sku, name) for everyone waiting on any of these skus."""
    if not skus:
        return []
    placeholders = ", ".join("?" for _ in skus)
    async with connect() as db:
        cursor = await db.execute(
            f"SELECT chat_id, sku, name FROM stock_subscriptions "
            f"WHERE sku IN ({placeholders})",
            skus,
        )
        return [tuple(row) for row in await cursor.fetchall()]


async def clear_subscriptions(pairs: list[tuple[int, str]]) -> None:
    """Drop subscriptions that have been fulfilled."""
    if not pairs:
        return
    async with connect() as db:
        await db.executemany(
            "DELETE FROM stock_subscriptions WHERE chat_id = ? AND sku = ?", pairs
        )
        await db.commit()
