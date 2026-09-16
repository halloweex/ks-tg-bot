"""Signed webhook messages handled — the SQLite side of DeliveryLedger.

See core/ports/webhooks.py for the contract: the key is the hash of the signed
bytes, a claim is a lease, and there are three states.

Every method is one connection and one commit. The prune DELETE that opens
`claim` takes SQLite's write lock for the rest of the transaction, so concurrent
claims queue behind each other; the INSERT OR IGNORE is atomic on its own
regardless.
"""
from __future__ import annotations

from core.ports.webhooks import Claim
from core.repos.base import connect

# How long a handled message is remembered. Longer than any retry schedule a
# webhook sender plausibly runs, short enough to keep the table small. A replay
# older than this is not recognised — the trade, written down in
# docs/loyalty-webhook.md.
RETENTION_DAYS = 30

# How long a claim holds before another attempt may take it over. Longer than
# handling one delivery takes (a few database writes), short enough that a
# retry after a crash is handled within minutes.
LEASE_SECONDS = 120


class SqliteDeliveryLedger:
    """Implements core.ports.webhooks.DeliveryLedger."""

    async def claim(self, source: str, digest: str, delivery_id: str) -> Claim:
        async with connect() as db:
            await db.execute(
                "DELETE FROM webhook_deliveries WHERE received_at < datetime('now', ?)",
                (f"-{RETENTION_DAYS} days",))
            cursor = await db.execute(
                "INSERT OR IGNORE INTO webhook_deliveries "
                "(source, signed_digest, delivery_id) VALUES (?, ?, ?)",
                (source, digest, delivery_id))
            if cursor.rowcount == 1:
                await db.commit()
                return "claimed"
            # Held already. Taken over only if nobody finished it and the lease
            # ran out — the attempt that held it died without releasing.
            cursor = await db.execute(
                "UPDATE webhook_deliveries SET received_at = datetime('now') "
                "WHERE source = ? AND signed_digest = ? AND done_at IS NULL "
                "AND received_at < datetime('now', ?)",
                (source, digest, f"-{LEASE_SECONDS} seconds"))
            if cursor.rowcount == 1:
                await db.commit()
                return "claimed"
            row = await (await db.execute(
                "SELECT done_at FROM webhook_deliveries "
                "WHERE source = ? AND signed_digest = ?", (source, digest))).fetchone()
            await db.commit()
            return "done" if row is not None and row[0] is not None else "busy"

    async def complete(self, source: str, digest: str) -> None:
        async with connect() as db:
            await db.execute(
                "UPDATE webhook_deliveries SET done_at = datetime('now') "
                "WHERE source = ? AND signed_digest = ?", (source, digest))
            await db.commit()

    async def release(self, source: str, digest: str) -> None:
        async with connect() as db:
            await db.execute(
                "DELETE FROM webhook_deliveries "
                "WHERE source = ? AND signed_digest = ? AND done_at IS NULL",
                (source, digest))
            await db.commit()
