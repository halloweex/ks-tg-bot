"""SQLite database initialization and connection management."""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

import aiosqlite
from loguru import logger

# Configurable so the DB can live on a mounted volume in Docker.
# Defaults to a file in the working directory for local runs.
DB_PATH = Path(os.getenv("BOT_DB_PATH", "bot_data.db"))

# How long a connection waits on a locked DB before erroring (ms).
_BUSY_TIMEOUT_MS = 5000


@asynccontextmanager
async def _connect() -> AsyncIterator[aiosqlite.Connection]:
    """Open a connection with a busy timeout so concurrent writers wait
    instead of failing with 'database is locked' during activity bursts.

    WAL journal mode is enabled once in init_db() and persists in the DB file,
    so readers here never block the background order-refresh writers.
    """
    db = await aiosqlite.connect(DB_PATH)
    try:
        await db.execute(f"PRAGMA busy_timeout = {_BUSY_TIMEOUT_MS}")
        yield db
    finally:
        await db.close()

_CREATE_USERS = """
CREATE TABLE IF NOT EXISTS users (
    chat_id    INTEGER PRIMARY KEY,
    phone      TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

_CREATE_OPT_OUT = """
CREATE TABLE IF NOT EXISTS opt_out (
    chat_id      INTEGER PRIMARY KEY,
    opted_out_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

_CREATE_ORDERS = """
CREATE TABLE IF NOT EXISTS orders (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id         INTEGER NOT NULL REFERENCES users(chat_id),
    source          TEXT NOT NULL,
    source_order_id TEXT NOT NULL,
    order_name      TEXT DEFAULT '',
    status_name     TEXT DEFAULT '',
    grand_total     REAL DEFAULT 0,
    currency        TEXT DEFAULT 'грн',
    ordered_at      TEXT DEFAULT '',
    products_json   TEXT DEFAULT '[]',
    buyer_name      TEXT DEFAULT '',
    payment_status  TEXT DEFAULT '',
    tracking_code   TEXT DEFAULT '',
    shipping_status TEXT DEFAULT '',
    delivery_city   TEXT DEFAULT '',
    receive_point   TEXT DEFAULT '',
    recipient_name  TEXT DEFAULT '',
    synced_at       TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(source, source_order_id)
);
"""


# Durable broadcast: a job + a per-recipient progress row so a broadcast
# interrupted by a restart/redeploy resumes exactly where it stopped.
_CREATE_BROADCAST_JOBS = """
CREATE TABLE IF NOT EXISTS broadcast_jobs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    text        TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'running',   -- running | done
    created_by  INTEGER,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    finished_at TEXT
);
"""

_CREATE_BROADCAST_TARGETS = """
CREATE TABLE IF NOT EXISTS broadcast_targets (
    job_id     INTEGER NOT NULL REFERENCES broadcast_jobs(id),
    chat_id    INTEGER NOT NULL,
    status     TEXT NOT NULL DEFAULT 'pending',    -- pending | sent | failed | blocked
    error      TEXT,
    updated_at TEXT,
    PRIMARY KEY (job_id, chat_id)
);
"""


_MIGRATIONS = [
    "ALTER TABLE users ADD COLUMN full_name TEXT",
    "ALTER TABLE users ADD COLUMN email TEXT",
    "ALTER TABLE users ADD COLUMN updated_at TEXT",
]


async def init_db() -> None:
    """Create the database and initialize tables if they don't exist.

    Safe to call multiple times — uses IF NOT EXISTS.
    Runs column migrations with try/except (duplicate column → OperationalError, ignored).
    """
    async with _connect() as db:
        # WAL lets readers and the background-refresh writer run concurrently.
        # This is a persistent DB property — set once, stays across connections.
        await db.execute("PRAGMA journal_mode = WAL")
        await db.execute(_CREATE_USERS)
        await db.execute(_CREATE_OPT_OUT)
        await db.execute(_CREATE_ORDERS)
        await db.execute(_CREATE_BROADCAST_JOBS)
        await db.execute(_CREATE_BROADCAST_TARGETS)
        for migration in _MIGRATIONS:
            try:
                await db.execute(migration)
            except Exception:  # noqa: BLE001 — OperationalError if column exists
                pass
        # Order lookups always filter by chat_id; without this they full-scan.
        await db.execute(
            "CREATE INDEX IF NOT EXISTS ix_orders_chat_id ON orders(chat_id)"
        )
        # Resuming a job scans its still-pending targets.
        await db.execute(
            "CREATE INDEX IF NOT EXISTS ix_targets_status "
            "ON broadcast_targets(job_id, status)"
        )
        await db.commit()
    logger.info("Database initialized at {} (WAL mode)", DB_PATH)


async def save_user(
    chat_id: int,
    phone: str,
    *,
    full_name: str | None = None,
    email: str | None = None,
) -> None:
    """Persist a verified chat_id-to-phone mapping with optional profile fields.

    Uses INSERT OR REPLACE — chat_id is PRIMARY KEY, so re-verification
    overwrites the old row. Optional full_name/email are stored when provided.
    """
    async with _connect() as db:
        await db.execute(
            "INSERT OR REPLACE INTO users (chat_id, phone, full_name, email, updated_at) "
            "VALUES (?, ?, ?, ?, datetime('now'))",
            (chat_id, phone, full_name, email),
        )
        await db.commit()


async def get_user_phone(chat_id: int) -> str | None:
    """Return the phone number for a verified user, or None if not found."""
    async with _connect() as db:
        cursor = await db.execute(
            "SELECT phone FROM users WHERE chat_id = ?",
            (chat_id,),
        )
        row = await cursor.fetchone()
        return row[0] if row else None


async def get_user(chat_id: int) -> dict | None:
    """Return full user profile dict or None if not found."""
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT phone, full_name, email FROM users WHERE chat_id = ?",
            (chat_id,),
        )
        row = await cursor.fetchone()
        if not row:
            return None
        return {"phone": row["phone"], "full_name": row["full_name"], "email": row["email"]}


async def opt_out_user(chat_id: int) -> None:
    """Mark a user as opted out of broadcasts."""
    async with _connect() as db:
        await db.execute(
            "INSERT OR IGNORE INTO opt_out (chat_id) VALUES (?)",
            (chat_id,),
        )
        await db.commit()


async def opt_in_user(chat_id: int) -> None:
    """Remove a user from the opt-out list (re-subscribe)."""
    async with _connect() as db:
        await db.execute(
            "DELETE FROM opt_out WHERE chat_id = ?",
            (chat_id,),
        )
        await db.commit()


async def is_opted_out(chat_id: int) -> bool:
    """Check whether a user has opted out of broadcasts."""
    async with _connect() as db:
        cursor = await db.execute(
            "SELECT 1 FROM opt_out WHERE chat_id = ?",
            (chat_id,),
        )
        return await cursor.fetchone() is not None


async def get_broadcast_recipients() -> list[int]:
    """Return chat_ids of all users who have NOT opted out."""
    async with _connect() as db:
        cursor = await db.execute(
            "SELECT u.chat_id FROM users u "
            "WHERE u.chat_id NOT IN (SELECT chat_id FROM opt_out)",
        )
        rows = await cursor.fetchall()
        return [row[0] for row in rows]


_ORDER_COLUMNS = (
    "chat_id", "source", "source_order_id", "order_name", "status_name",
    "grand_total", "currency", "ordered_at", "products_json", "buyer_name",
    "payment_status", "tracking_code", "shipping_status", "delivery_city",
    "receive_point", "recipient_name",
)


async def upsert_orders(chat_id: int, orders: list[dict]) -> None:
    """Bulk upsert orders from API results into the local cache.

    Each dict must have keys matching _ORDER_COLUMNS.
    Uses INSERT OR REPLACE keyed on UNIQUE(source, source_order_id).
    """
    if not orders:
        return
    placeholders = ", ".join("?" for _ in _ORDER_COLUMNS)
    sql = (
        f"INSERT OR REPLACE INTO orders ({', '.join(_ORDER_COLUMNS)}, synced_at) "
        f"VALUES ({placeholders}, datetime('now'))"
    )
    async with _connect() as db:
        for order in orders:
            values = tuple(order.get(col, "") for col in _ORDER_COLUMNS)
            await db.execute(sql, values)
        await db.commit()


async def get_cached_orders(chat_id: int) -> list[dict]:
    """Return all cached orders for a user, newest first."""
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM orders WHERE chat_id = ? ORDER BY ordered_at DESC",
            (chat_id,),
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


async def get_orders_with_tracking(chat_id: int) -> list[dict]:
    """Return cached orders that have a non-empty tracking_code, newest first."""
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM orders WHERE chat_id = ? AND tracking_code != '' "
            "ORDER BY ordered_at DESC",
            (chat_id,),
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


async def get_last_sync_time(chat_id: int) -> str | None:
    """Return the most recent synced_at timestamp for a user's orders, or None."""
    async with _connect() as db:
        cursor = await db.execute(
            "SELECT MAX(synced_at) FROM orders WHERE chat_id = ?",
            (chat_id,),
        )
        row = await cursor.fetchone()
        return row[0] if row and row[0] else None


# ---------------------------------------------------------------------------
# Durable broadcast
# ---------------------------------------------------------------------------


async def create_broadcast_job(text: str, created_by: int) -> int:
    """Create a broadcast job and snapshot the current recipient list into
    broadcast_targets (all 'pending'). Returns the new job id.

    The recipient set is frozen at creation time so a later opt-out/opt-in
    can't change which people this job is responsible for.
    """
    async with _connect() as db:
        cursor = await db.execute(
            "INSERT INTO broadcast_jobs (text, created_by) VALUES (?, ?)",
            (text, created_by),
        )
        job_id = cursor.lastrowid
        await db.execute(
            "INSERT INTO broadcast_targets (job_id, chat_id) "
            "SELECT ?, chat_id FROM users "
            "WHERE chat_id NOT IN (SELECT chat_id FROM opt_out)",
            (job_id,),
        )
        await db.commit()
        return job_id


async def get_pending_targets(job_id: int) -> list[int]:
    """Return chat_ids of this job's targets that still need sending."""
    async with _connect() as db:
        cursor = await db.execute(
            "SELECT chat_id FROM broadcast_targets "
            "WHERE job_id = ? AND status = 'pending'",
            (job_id,),
        )
        return [row[0] for row in await cursor.fetchall()]


async def mark_target(
    job_id: int, chat_id: int, status: str, error: str | None = None
) -> None:
    """Record the delivery outcome for one recipient (sent/failed/blocked)."""
    async with _connect() as db:
        await db.execute(
            "UPDATE broadcast_targets SET status = ?, error = ?, "
            "updated_at = datetime('now') WHERE job_id = ? AND chat_id = ?",
            (status, error, job_id, chat_id),
        )
        await db.commit()


async def get_unfinished_broadcasts() -> list[dict]:
    """Return jobs still marked 'running' (to resume after a restart)."""
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT id, text, created_by FROM broadcast_jobs "
            "WHERE status = 'running' ORDER BY id"
        )
        return [dict(row) for row in await cursor.fetchall()]


async def finish_broadcast_job(job_id: int) -> None:
    """Mark a job done once all its targets are processed."""
    async with _connect() as db:
        await db.execute(
            "UPDATE broadcast_jobs SET status = 'done', "
            "finished_at = datetime('now') WHERE id = ?",
            (job_id,),
        )
        await db.commit()


async def broadcast_job_stats(job_id: int) -> dict:
    """Return counts per status for a job: sent/failed/blocked/pending."""
    async with _connect() as db:
        cursor = await db.execute(
            "SELECT status, COUNT(*) FROM broadcast_targets "
            "WHERE job_id = ? GROUP BY status",
            (job_id,),
        )
        stats = {"sent": 0, "failed": 0, "blocked": 0, "pending": 0}
        for status, count in await cursor.fetchall():
            stats[status] = count
        return stats
