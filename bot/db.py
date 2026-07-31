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
    -- Same physical order seen in two systems shares this id (Shopify's numeric
    -- order id, which KeyCRM mirrors as global_source_uuid). Empty for orders
    -- that exist in one system only. See dedupe_shadowed_orders().
    external_id     TEXT DEFAULT '',
    order_name      TEXT DEFAULT '',
    status_name     TEXT DEFAULT '',
    status_group_id INTEGER DEFAULT 0,
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


# Product analytics. chat_id is stored deliberately: without it there are no
# cohorts and no way to tell a returning user from a new one, which is exactly
# what the roadmap decisions need. It is the same id already held in `users`,
# so this adds no identifier the bot did not already have.
_CREATE_EVENTS = """
CREATE TABLE IF NOT EXISTS events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id    INTEGER,
    event      TEXT NOT NULL,
    meta       TEXT DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


# Last known availability per sku, so a poll can tell a restock from a no-change.
# Only what the transition test needs — the catalogue itself lives in KeyCRM.
_CREATE_STOCK_LEVELS = """
CREATE TABLE IF NOT EXISTS stock_levels (
    sku        TEXT PRIMARY KEY,
    available  INTEGER NOT NULL,
    checked_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

# One row per person waiting for one product. Removed once notified: the promise
# is "tell me when it is back", not "watch this forever".
_CREATE_STOCK_SUBSCRIPTIONS = """
CREATE TABLE IF NOT EXISTS stock_subscriptions (
    chat_id    INTEGER NOT NULL,
    sku        TEXT NOT NULL,
    name       TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (chat_id, sku)
);
"""


# A customer asking for a discount on what they buy most. Deliberately a request
# a manager answers, not an automatically issued code: there is no discount
# policy yet, and inventing one in the bot would commit the business to it.
_CREATE_DISCOUNT_REQUESTS = """
CREATE TABLE IF NOT EXISTS discount_requests (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id       INTEGER NOT NULL,
    products_json TEXT NOT NULL DEFAULT '[]',
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


_MIGRATIONS = [
    "ALTER TABLE users ADD COLUMN full_name TEXT",
    "ALTER TABLE users ADD COLUMN email TEXT",
    "ALTER TABLE users ADD COLUMN updated_at TEXT",
    "ALTER TABLE orders ADD COLUMN external_id TEXT DEFAULT ''",
    # NULL means "never chosen": the user's Telegram language still decides.
    # Once they pick one explicitly it is stored and wins from then on.
    "ALTER TABLE users ADD COLUMN language TEXT",
    "ALTER TABLE orders ADD COLUMN status_group_id INTEGER DEFAULT 0",
]

# Rows cached before external_id existed can't be deduped by it, so they'd keep
# showing a second copy of the order forever. The Shopify side is recoverable
# from the stored gid, and the KeyCRM side is refilled on the next refresh —
# together that's enough for dedupe_shadowed_orders to sweep the old pairs.
# Idempotent: matches zero rows once it has run.
_BACKFILL_SHOPIFY_EXTERNAL_ID = """
UPDATE orders
   SET external_id = replace(source_order_id, 'gid://shopify/Order/', '')
 WHERE source = 'shopify'
   AND (external_id IS NULL OR external_id = '')
   AND source_order_id LIKE 'gid://shopify/Order/%'
"""


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
        await db.execute(_CREATE_EVENTS)
        await db.execute(_CREATE_STOCK_LEVELS)
        await db.execute(_CREATE_STOCK_SUBSCRIPTIONS)
        await db.execute(_CREATE_DISCOUNT_REQUESTS)
        for migration in _MIGRATIONS:
            try:
                await db.execute(migration)
            except Exception:  # noqa: BLE001 — OperationalError if column exists
                pass
        await db.execute(_BACKFILL_SHOPIFY_EXTERNAL_ID)
        # Order lookups always filter by chat_id; without this they full-scan.
        await db.execute(
            "CREATE INDEX IF NOT EXISTS ix_orders_chat_id ON orders(chat_id)"
        )
        # Cross-system dedup matches rows by external_id within one user.
        await db.execute(
            "CREATE INDEX IF NOT EXISTS ix_orders_external "
            "ON orders(chat_id, external_id)"
        )
        # Every analytics query slices by event name over a time window, and
        # per-user questions (returning? cohort?) scan one chat_id.
        await db.execute(
            "CREATE INDEX IF NOT EXISTS ix_events_name_time ON events(event, created_at)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS ix_events_chat ON events(chat_id, created_at)"
        )
        # A restock fans out to everyone waiting on that sku.
        await db.execute(
            "CREATE INDEX IF NOT EXISTS ix_stock_subs_sku ON stock_subscriptions(sku)"
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

    REPLACE writes a whole new row, so any column not listed here would be reset
    to its default. The chosen language and the original signup date are carried
    over explicitly: losing them on a phone re-verification would silently flip
    the user back to Ukrainian and destroy the signup cohort.
    """
    async with _connect() as db:
        await db.execute(
            "INSERT OR REPLACE INTO users "
            "(chat_id, phone, full_name, email, language, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, "
            "  (SELECT language FROM users WHERE chat_id = ?), "
            "  COALESCE((SELECT created_at FROM users WHERE chat_id = ?), datetime('now')), "
            "  datetime('now'))",
            (chat_id, phone, full_name, email, chat_id, chat_id),
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


async def get_user_language(chat_id: int) -> str | None:
    """Return the language the user explicitly chose, or None if they never did.

    None is meaningful: it means fall back to their Telegram language_code.
    """
    async with _connect() as db:
        cursor = await db.execute(
            "SELECT language FROM users WHERE chat_id = ?",
            (chat_id,),
        )
        row = await cursor.fetchone()
        return row[0] if row and row[0] else None


async def set_user_language(chat_id: int, lang: str) -> None:
    """Persist an explicit language choice.

    Only touches the language column — save_user() does INSERT OR REPLACE and
    would wipe the profile fields if used here.
    """
    async with _connect() as db:
        await db.execute(
            "UPDATE users SET language = ?, updated_at = datetime('now') "
            "WHERE chat_id = ?",
            (lang, chat_id),
        )
        await db.commit()


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
    "chat_id", "source", "source_order_id", "external_id", "order_name",
    "status_name", "status_group_id", "grand_total", "currency", "ordered_at",
    "products_json",
    "buyer_name", "payment_status", "tracking_code", "shipping_status",
    "delivery_city", "receive_point", "recipient_name",
)

# Seeded by the /demo admin command, never by a sync. Kept out of the real
# sources so it can be deleted precisely and can never collide with a genuine
# order id.
DEMO_SOURCE = "demo"

# KeyCRM's cancelled / returned / out-of-stock status family. An order in this
# group is not in transit, so it must not appear under delivery tracking as if
# it were on its way.
CANCELLED_STATUS_GROUP = 6

# A Shopify row is redundant once KeyCRM reports the same order: KeyCRM is the
# operational system of record (fulfilment status, tracking code, delivery
# point), and it carries the store order number too, so nothing is lost.
_DELETE_SHADOWED = """
DELETE FROM orders
 WHERE chat_id = ?
   AND source = 'shopify'
   AND external_id != ''
   AND external_id IN (
         SELECT external_id FROM orders
          WHERE chat_id = ? AND source = 'keycrm' AND external_id != ''
       )
"""


async def upsert_orders(chat_id: int, orders: list[dict]) -> None:
    """Bulk upsert orders from API results into the local cache.

    Each dict must have keys matching _ORDER_COLUMNS.
    Uses INSERT OR REPLACE keyed on UNIQUE(source, source_order_id).

    Afterwards drops any Shopify row shadowed by a KeyCRM row for the same
    physical order, so one order is never shown twice with two statuses. The
    sweep runs on every refresh, which also cleans out duplicates written by
    earlier versions of the bot.
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
        await db.execute(_DELETE_SHADOWED, (chat_id, chat_id))
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
    """Cached orders with a tracking code that are still worth tracking.

    Cancelled and returned orders keep their tracking code, so without the
    status-group filter they showed up under delivery as active shipments.
    """
    async with _connect() as db:
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


# ---------------------------------------------------------------------------
# Product analytics
# ---------------------------------------------------------------------------


async def log_event(chat_id: int | None, event: str, meta: str = "{}") -> None:
    """Append one analytics event. Never called directly from a handler —
    go through bot.analytics.track(), which makes it fire-and-forget."""
    async with _connect() as db:
        await db.execute(
            "INSERT INTO events (chat_id, event, meta) VALUES (?, ?, ?)",
            (chat_id, event, meta),
        )
        await db.commit()


async def event_counts(days: int = 7) -> list[tuple[str, int, int]]:
    """(event, occurrences, distinct users) over the last `days`."""
    async with _connect() as db:
        cursor = await db.execute(
            "SELECT event, COUNT(*), COUNT(DISTINCT chat_id) FROM events "
            "WHERE created_at >= datetime('now', ?) "
            "GROUP BY event ORDER BY COUNT(*) DESC",
            (f"-{days} days",),
        )
        return [tuple(row) for row in await cursor.fetchall()]


async def funnel_counts(days: int = 30) -> dict[str, int]:
    """Distinct users reaching each step of the onboarding funnel."""
    steps = ("start", "contact_shared", "registered", "orders_viewed")
    out: dict[str, int] = {}
    async with _connect() as db:
        for step in steps:
            cursor = await db.execute(
                "SELECT COUNT(DISTINCT chat_id) FROM events "
                "WHERE event = ? AND created_at >= datetime('now', ?)",
                (step, f"-{days} days"),
            )
            row = await cursor.fetchone()
            out[step] = row[0] if row else 0
        return out


async def lookup_miss_rate(days: int = 30) -> tuple[int, int]:
    """(lookups that found nothing, total lookups) over the window.

    This is the number that decides whether the phone-match assumption holds in
    practice: a user who shares a contact and sees no orders is the single worst
    outcome of the whole flow, and until now it was invisible.

    Only real API lookups count. A view served from cache says nothing about
    whether the phone matched, and including those would dilute the rate with
    repeat taps by the same happy user.
    """
    async with _connect() as db:
        cursor = await db.execute(
            "SELECT COUNT(*) FILTER (WHERE json_extract(meta, '$.found') = 0), "
            "       COUNT(*) "
            "  FROM events "
            " WHERE event = 'orders_viewed' "
            "   AND json_extract(meta, '$.cached') = 0 "
            "   AND created_at >= datetime('now', ?)",
            (f"-{days} days",),
        )
        row = await cursor.fetchone()
        return (row[0] or 0, row[1] or 0)


async def returning_users(days: int = 30) -> tuple[int, int]:
    """(users active on >1 distinct day, users active at all) in the window."""
    async with _connect() as db:
        cursor = await db.execute(
            "SELECT COUNT(*), SUM(CASE WHEN d > 1 THEN 1 ELSE 0 END) FROM ("
            "  SELECT chat_id, COUNT(DISTINCT date(created_at)) AS d FROM events"
            "   WHERE chat_id IS NOT NULL AND created_at >= datetime('now', ?)"
            "   GROUP BY chat_id)",
            (f"-{days} days",),
        )
        row = await cursor.fetchone()
        return ((row[1] or 0), (row[0] or 0))


async def delete_demo_orders(chat_id: int) -> int:
    """Remove the demo orders seeded for one chat. Returns how many went."""
    async with _connect() as db:
        cursor = await db.execute(
            "DELETE FROM orders WHERE chat_id = ? AND source = ?",
            (chat_id, DEMO_SOURCE),
        )
        await db.commit()
        return cursor.rowcount or 0


async def count_demo_orders(chat_id: int) -> int:
    """How many demo orders are currently seeded for this chat."""
    async with _connect() as db:
        cursor = await db.execute(
            "SELECT COUNT(*) FROM orders WHERE chat_id = ? AND source = ?",
            (chat_id, DEMO_SOURCE),
        )
        row = await cursor.fetchone()
        return row[0] if row else 0


# ---------------------------------------------------------------------------
# Back-in-stock subscriptions
# ---------------------------------------------------------------------------


async def get_stock_levels() -> dict[str, int]:
    """The last recorded availability per sku."""
    async with _connect() as db:
        cursor = await db.execute("SELECT sku, available FROM stock_levels")
        return {row[0]: row[1] for row in await cursor.fetchall()}


async def save_stock_levels(levels: dict[str, int]) -> None:
    """Replace the recorded availability with a fresh snapshot."""
    if not levels:
        return
    async with _connect() as db:
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
    async with _connect() as db:
        await db.execute(
            "INSERT INTO stock_subscriptions (chat_id, sku, name) VALUES (?, ?, ?) "
            "ON CONFLICT(chat_id, sku) DO UPDATE SET name = excluded.name",
            (chat_id, sku, name),
        )
        await db.commit()


async def remove_stock_subscription(chat_id: int, sku: str) -> None:
    async with _connect() as db:
        await db.execute(
            "DELETE FROM stock_subscriptions WHERE chat_id = ? AND sku = ?",
            (chat_id, sku),
        )
        await db.commit()


async def get_subscribed_skus(chat_id: int) -> set[str]:
    """Which skus this person is already waiting on."""
    async with _connect() as db:
        cursor = await db.execute(
            "SELECT sku FROM stock_subscriptions WHERE chat_id = ?", (chat_id,)
        )
        return {row[0] for row in await cursor.fetchall()}


async def subscribers_for(skus: list[str]) -> list[tuple[int, str, str]]:
    """(chat_id, sku, name) for everyone waiting on any of these skus."""
    if not skus:
        return []
    placeholders = ", ".join("?" for _ in skus)
    async with _connect() as db:
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
    async with _connect() as db:
        await db.executemany(
            "DELETE FROM stock_subscriptions WHERE chat_id = ? AND sku = ?", pairs
        )
        await db.commit()


async def recent_discount_request(chat_id: int, days: int = 7) -> bool:
    """True if this customer already asked within the window.

    Stops a second tap, or a bored customer, from filling the support chat with
    the same request.
    """
    async with _connect() as db:
        cursor = await db.execute(
            "SELECT 1 FROM discount_requests WHERE chat_id = ? "
            "AND created_at >= datetime('now', ?) LIMIT 1",
            (chat_id, f"-{days} days"),
        )
        return await cursor.fetchone() is not None


async def add_discount_request(chat_id: int, products_json: str) -> None:
    async with _connect() as db:
        await db.execute(
            "INSERT INTO discount_requests (chat_id, products_json) VALUES (?, ?)",
            (chat_id, products_json),
        )
        await db.commit()
