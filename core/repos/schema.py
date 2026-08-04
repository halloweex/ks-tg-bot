"""Every table the bot has, and the migrations that got them there.

Kept apart from the repositories on purpose: this module is read once at
startup, they are read on every request, and mixing the two is what made
bot/db.py 1200 lines.
"""
from __future__ import annotations

import aiosqlite
from loguru import logger

from core.repos import base
from core.repos.base import connect

# `base.DB_PATH` and not `from base import DB_PATH`: configure() rebinds the
# name in its own module, so a value imported here would freeze the default and
# report a database nobody is using. connect() reads it at call time, which is
# why importing that one by name is safe.

_CREATE_USERS = """
CREATE TABLE IF NOT EXISTS users (
    chat_id    INTEGER PRIMARY KEY,
    phone      TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

# Columns added after the first release. Applied to an existing database by
# migration 1, and to a new one right here — the CREATE statements above stay
# at their original shape so the two paths cannot drift into different tables.
_LATE_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("users", "full_name", "TEXT"),
    ("users", "email", "TEXT"),
    ("users", "updated_at", "TEXT"),
    # NULL means "never chosen": the user's Telegram language still decides.
    # Once they pick one explicitly it is stored and wins from then on.
    ("users", "language", "TEXT"),
    # Where this person came from — the payload of the deep link they opened,
    # empty for anyone who found the bot by themselves. Written once, at
    # registration, so it stays the *first* touch rather than the latest.
    ("users", "source", "TEXT DEFAULT ''"),
    ("orders", "external_id", "TEXT DEFAULT ''"),
    ("orders", "status_group_id", "INTEGER DEFAULT 0"),
)

_CREATE_OPT_OUT = """
CREATE TABLE IF NOT EXISTS opt_out (
    chat_id      INTEGER PRIMARY KEY,
    opted_out_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

# The orders table as it must end up. Rebuilt through this exact statement by
# migration 2, so there is one definition of the table and not two.
#
# The key is (chat_id, source, source_order_id) and not (source, source_order_id):
# an order id is unique in the CRM, not per Telegram account, and rows are
# written with INSERT OR REPLACE. Two accounts holding the same phone — a
# household, a recycled mobile number, a customer who moved to a new Telegram —
# meant the second one's refresh silently reassigned the first one's rows to
# itself, and the first customer's history went empty.
_ORDERS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS {name} (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id         INTEGER NOT NULL REFERENCES users(chat_id),
    source          TEXT NOT NULL,
    source_order_id TEXT NOT NULL,
    external_id     TEXT DEFAULT '',
    -- Identity of the physical order across systems, and which source's data
    -- currently occupies the row. See core/domain/order.py.
    merge_key       TEXT NOT NULL DEFAULT '',
    source_rank     INTEGER NOT NULL DEFAULT 0,
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
    UNIQUE(chat_id, source, source_order_id)
);
"""


# external_id: the same physical order seen in two systems shares this id
# (Shopify's numeric order id, which KeyCRM mirrors as global_source_uuid).
# Empty for orders that exist in one system only. Feeds merge_key
# (core/domain/order.py).
_CREATE_ORDERS = _ORDERS_TABLE_SQL.format(name="orders")


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


# Which customer a message in the support chat belongs to. Every bot-sent
# message of one support request is recorded — the metadata line, the forwarded
# copy and the instruction — so a manager replying to any of the three reaches
# the right person.
#
# Replaces guessing from the message itself. `forward_from` is empty whenever
# the customer has forwarding privacy on, which is the default for many, and
# the metadata line is only readable if the manager happened to reply to that
# particular message rather than to the forwarded text.
_CREATE_SUPPORT_THREADS = """
CREATE TABLE IF NOT EXISTS support_threads (
    admin_message_id INTEGER PRIMARY KEY,
    chat_id          INTEGER NOT NULL,
    created_at       TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


# An album arrives as several separate messages sharing a media_group_id, with
# no signal marking the last one. This records that a chat's album is already
# being forwarded, so the messages after the first join the same thread instead
# of being dropped. Short-lived by nature — the parts arrive within a second.
_CREATE_SUPPORT_ALBUMS = """
CREATE TABLE IF NOT EXISTS support_albums (
    chat_id        INTEGER NOT NULL,
    media_group_id TEXT NOT NULL,
    created_at     TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (chat_id, media_group_id)
);
"""


# Conversation state, so a deploy does not drop everyone mid-flow. One row per
# conversation; the key is built in bot/fsm_storage.py from every field aiogram
# uses to tell conversations apart.
_CREATE_FSM_STATE = """
CREATE TABLE IF NOT EXISTS fsm_state (
    key        TEXT PRIMARY KEY,
    state      TEXT,
    data       TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
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


# How far the incremental sync has read, and whether it is still reading. One
# row per source. See core/repos/sync_state.py for what each column means and
# why the alert reads last_success_at rather than last_error.
_CREATE_SYNC_STATE = """
CREATE TABLE IF NOT EXISTS sync_state (
    source          TEXT PRIMARY KEY,
    cursor          TEXT,
    last_run_at     TEXT,
    last_success_at TEXT,
    last_error      TEXT,
    last_full_at    TEXT
);
"""


# --------------------------------------------------------------------------
# Schema versions
# --------------------------------------------------------------------------
#
# Versions are recorded in SQLite's own `PRAGMA user_version`, so the schema
# needs no bookkeeping table and cannot disagree with itself. A migration runs
# once, in order, inside the same transaction as everything else in init_db():
# a failure leaves the database exactly as it was and the bot refuses to start,
# which is the outcome to want — a half-migrated database that keeps serving is
# how a cache becomes wrong without anyone noticing.
#
# That last sentence was false until the connection became transactional.
# sqlite3's legacy mode opens an implicit transaction before DML but not before
# DDL, so every ALTER and CREATE here committed itself the moment it ran: a
# crash halfway through migration 2 left a partly filled `orders_migrated`
# behind, the next start died on "table orders_migrated already exists", and
# `restart: always` turned that into a crash loop with no way back — the image
# is built on the production host, so there is no previous tag to roll to.
# connect(transactional=True) plus the DROP below are what make the comment
# true. Verified by killing the process mid-copy and restarting it.
#
# The previous mechanism was a list of ALTER statements under `except: pass`.
# It could not express this change (SQLite cannot alter a UNIQUE constraint),
# and it silently swallowed real failures — a full disk logged success.

SCHEMA_VERSION = 7


async def _columns(db: aiosqlite.Connection, table: str) -> set[str]:
    cursor = await db.execute(f"PRAGMA table_info({table})")
    return {row[1] for row in await cursor.fetchall()}


async def _add_late_columns(db: aiosqlite.Connection) -> None:
    """Add every column in _LATE_COLUMNS that the table does not already have.

    Used by both paths on purpose. A fresh database is stamped with the current
    version instead of being migrated, so migration 1 never runs on it — and
    without this call it was left with `users` holding chat_id, phone and
    created_at alone. Every language lookup, every profile write and every
    language change then failed with "no such column" from the first /start.
    Production never hit it because it predates those columns and received them
    through the migration; only a genuinely new deployment did.
    """
    for table, column, decl in _LATE_COLUMNS:
        if column not in await _columns(db, table):
            await db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


async def _migration_1_late_columns(db: aiosqlite.Connection) -> None:
    """Columns added after the first release, plus the external_id backfill."""
    await _add_late_columns(db)
    # Rows cached before external_id existed can't be deduped by it, so they'd
    # keep showing a second copy of the order forever. The Shopify side is
    # recoverable from the stored gid; the KeyCRM side refills on the next
    # refresh. Idempotent: matches zero rows once it has run.
    await db.execute(
        "UPDATE orders "
        "   SET external_id = replace(source_order_id, 'gid://shopify/Order/', '') "
        " WHERE source = 'shopify' "
        "   AND (external_id IS NULL OR external_id = '') "
        "   AND source_order_id LIKE 'gid://shopify/Order/%'"
    )


async def _migration_2_orders_owned_per_chat(db: aiosqlite.Connection) -> None:
    """Rebuild `orders` with chat_id in the unique key.

    SQLite cannot alter a constraint, so the table is recreated and copied.
    `id` is copied too: it is what the expand/collapse buttons carry, and
    renumbering would point every button already on someone's screen at a
    different order.
    """
    existing = await _columns(db, "orders")
    # Idempotent restart: a leftover from an interrupted attempt is dropped
    # rather than reused, because IF NOT EXISTS alone would keep a partly
    # copied table and the INSERT below would then duplicate every row it
    # already holds. Inside the transaction this DROP is invisible to anyone
    # else; on a healthy database it matches nothing.
    await db.execute("DROP TABLE IF EXISTS orders_migrated")
    await db.execute(_ORDERS_TABLE_SQL.format(name="orders_migrated"))
    shared = [c for c in await _columns(db, "orders_migrated") if c in existing]
    columns = ", ".join(shared)
    # No row can be lost: the old key was unique across all chats, so any set
    # of rows unique under it is also unique under the narrower new key.
    await db.execute(
        f"INSERT INTO orders_migrated ({columns}) SELECT {columns} FROM orders"
    )
    await db.execute("DROP TABLE orders")
    await db.execute("ALTER TABLE orders_migrated RENAME TO orders")


async def _migration_3_merge_key(db: aiosqlite.Connection) -> None:
    """Give every order a cross-system identity and collapse the duplicates.

    Replaces _DELETE_SHADOWED, which deleted the Shopify copy on every refresh
    for as long as the bot ran. A unique index does the same job once, in the
    schema, where it also stops the duplicate being written in the first place.

    The index is scoped to (chat_id, merge_key) and not to merge_key alone,
    which is what docs/architecture.md 3.1 asks for. Orders still belong to a
    chat and are still fetched per chat; a global key would mean two accounts
    sharing a phone collapse onto one row, which is the bug migration 2 exists
    to fix. It widens to a global key in the stage that makes orders.user_id
    nullable, and not before.
    """
    from core.domain.order import merge_key as _key, source_rank as _rank

    existing = await _columns(db, "orders")
    for column, decl in (("merge_key", "TEXT NOT NULL DEFAULT ''"),
                         ("source_rank", "INTEGER NOT NULL DEFAULT 0")):
        if column not in existing:
            await db.execute(f"ALTER TABLE orders ADD COLUMN {column} {decl}")

    cursor = await db.execute(
        "SELECT id, source, source_order_id, external_id FROM orders")
    rows = await cursor.fetchall()
    for row_id, source, source_order_id, external_id in rows:
        await db.execute(
            "UPDATE orders SET merge_key = ?, source_rank = ? WHERE id = ?",
            (_key(source, source_order_id, external_id), _rank(source), row_id),
        )

    # Collapse what _DELETE_SHADOWED used to sweep on every refresh: within one
    # chat, keep the highest-ranked copy of each key, oldest id breaking ties so
    # the id an on-screen button carries survives where it can.
    await db.execute(
        "DELETE FROM orders WHERE id NOT IN ("
        "  SELECT id FROM orders o WHERE o.id = ("
        "    SELECT i.id FROM orders i"
        "     WHERE i.chat_id = o.chat_id AND i.merge_key = o.merge_key"
        "     ORDER BY i.source_rank DESC, i.id ASC LIMIT 1))"
    )
    # Partial on purpose. An image rolled back past this migration runs code
    # that knows nothing about merge_key, writes '' for every order and uses
    # INSERT OR REPLACE — so a full index would make each order replace the
    # previous one and leave the chat holding a single row. Silently, because
    # REPLACE resolves the conflict instead of raising. Measured on a copy of
    # production: three orders in, one row out. Excluding the empty key leaves
    # those writes to the table's own UNIQUE(chat_id, source, source_order_id),
    # which is exactly what the old code expects, and a rollback is boring
    # again.
    await db.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_orders_merge "
        "ON orders(chat_id, merge_key) WHERE merge_key != ''"
    )


async def _migration_4_support_threads(db: aiosqlite.Connection) -> None:
    """Add the table that maps a support-chat message to its customer.

    Nothing to backfill: the mapping for messages sent before this existed
    cannot be reconstructed, so old threads keep falling back to the two
    guesses in the handler until they age out of use.
    """
    await db.execute(_CREATE_SUPPORT_THREADS)


async def _migration_5_fsm_state(db: aiosqlite.Connection) -> None:
    """Add persistent conversation state.

    Nothing to backfill: whatever was in memory is gone by the time this runs.
    """
    await db.execute(_CREATE_FSM_STATE)


async def _migration_6_support_albums(db: aiosqlite.Connection) -> None:
    """Track an album already being forwarded, so its later parts are not lost."""
    await db.execute(_CREATE_SUPPORT_ALBUMS)


async def _migration_7_sync_state(db: aiosqlite.Connection) -> None:
    """Add the row the incremental sync keeps its cursor in.

    Nothing to backfill, and deliberately no seeded cursor either: an absent one
    means "never swept", which is what makes the first sweep read the whole
    reconciliation window instead of the last two minutes.
    """
    await db.execute(_CREATE_SYNC_STATE)


# (version, name, coroutine). Append only; never edit one that has shipped.
_MIGRATIONS: tuple[tuple[int, str, object], ...] = (
    (1, "late columns", _migration_1_late_columns),
    (2, "orders owned per chat", _migration_2_orders_owned_per_chat),
    (3, "merge_key identity", _migration_3_merge_key),
    (4, "support threads", _migration_4_support_threads),
    (5, "persistent fsm state", _migration_5_fsm_state),
    (6, "support albums", _migration_6_support_albums),
    (7, "sync state", _migration_7_sync_state),
)


async def _migrate(db: aiosqlite.Connection) -> None:
    """Bring an existing database up to SCHEMA_VERSION."""
    cursor = await db.execute("PRAGMA user_version")
    version = (await cursor.fetchone())[0]
    if version >= SCHEMA_VERSION:
        return
    for target, name, run in _MIGRATIONS:
        if version >= target:
            continue
        logger.info("Applying schema migration {} — {}", target, name)
        await run(db)
        # PRAGMA takes no parameters; `target` is an int literal from this file.
        await db.execute(f"PRAGMA user_version = {target}")
        version = target
    logger.info("Schema at version {}", version)


async def init_db() -> None:
    """Create the database if absent, migrate it if present.

    Safe to call on every start. A database created here is stamped with the
    current version rather than migrated: the CREATE statements already carry
    the final shape, and replaying the history onto a table that never had it
    would be a longer way to the same place — with more to go wrong.
    """
    # WAL lets readers and the background-refresh writer run concurrently.
    # This is a persistent DB property — set once, stays across connections.
    # It gets its own connection because SQLite refuses to enter WAL from
    # inside a transaction, and the block below runs in one.
    async with connect() as db:
        await db.execute("PRAGMA journal_mode = WAL")

    # One transaction for the whole schema: tables, migrations, indexes and the
    # version stamp commit together or not at all.
    async with connect(transactional=True) as db:
        cursor = await db.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name = 'orders'"
        )
        fresh = (await cursor.fetchone())[0] == 0
        await db.execute(_CREATE_USERS)
        await db.execute(_CREATE_OPT_OUT)
        await db.execute(_CREATE_ORDERS)
        await db.execute(_CREATE_BROADCAST_JOBS)
        await db.execute(_CREATE_BROADCAST_TARGETS)
        await db.execute(_CREATE_EVENTS)
        await db.execute(_CREATE_STOCK_LEVELS)
        await db.execute(_CREATE_STOCK_SUBSCRIPTIONS)
        await db.execute(_CREATE_DISCOUNT_REQUESTS)
        await db.execute(_CREATE_SUPPORT_THREADS)
        await db.execute(_CREATE_FSM_STATE)
        await db.execute(_CREATE_SUPPORT_ALBUMS)
        await db.execute(_CREATE_SYNC_STATE)

        if fresh:
            # The CREATE statements above deliberately keep their original
            # shape so the two paths cannot drift; the columns added later are
            # applied here, exactly as migration 1 applies them to an existing
            # database.
            await _add_late_columns(db)
            await db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        else:
            # Before the indexes below: rebuilding a table drops its indexes,
            # and they are recreated from here on the way out.
            await _migrate(db)

        # Order lookups always filter by chat_id; without this they full-scan.
        await db.execute(
            "CREATE INDEX IF NOT EXISTS ix_orders_chat_id ON orders(chat_id)"
        )
        # One row per physical order per chat. Replaces the per-refresh sweep.
        await db.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS ux_orders_merge "
            "ON orders(chat_id, merge_key) WHERE merge_key != ''"
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
    logger.info("Database initialized at {} (WAL mode)", base.DB_PATH)
