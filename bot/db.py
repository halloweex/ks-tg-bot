"""SQLite database initialization and connection management."""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

import aiosqlite
from loguru import logger

from core.domain.order import merge_key, source_rank

# Where the database lives. The default is for local runs; production passes a
# path on the mounted volume. Set once at startup from Settings — see
# configure() — rather than read from the environment here, so that the
# environment has exactly one reader in the whole tree.
DB_PATH = Path("bot_data.db")


def configure(path: str | Path) -> None:
    """Point the module at a database file. Call before init_db()."""
    global DB_PATH
    DB_PATH = Path(path)

# How long a connection waits on a locked DB before erroring (ms).
_BUSY_TIMEOUT_MS = 5000


@asynccontextmanager
async def _connect(*, transactional: bool = False) -> AsyncIterator[aiosqlite.Connection]:
    """Open a connection with a busy timeout so concurrent writers wait
    instead of failing with 'database is locked' during activity bursts.

    WAL journal mode is enabled once in init_db() and persists in the DB file,
    so readers here never block the background order-refresh writers.

    transactional=True switches the connection out of sqlite3's legacy mode,
    where an implicit BEGIN is issued before DML but *not* before DDL, into
    PEP 249 semantics, where a single transaction covers both. Only the schema
    work in init_db() needs it — every other caller writes one statement at a
    time and legacy mode is what it has always run under.

    Two things behave differently on a transactional connection, both verified
    on python 3.14.2 / sqlite 3.51.2:
      - `PRAGMA journal_mode = WAL` raises "cannot change into wal mode from
        within a transaction" as the first statement, and silently reports
        'delete' after any other statement has opened one. WAL is therefore
        set on its own connection in init_db().
      - closing without commit rolls the whole thing back, which is the point.
    """
    kwargs = {"autocommit": False} if transactional else {}
    db = await aiosqlite.connect(DB_PATH, **kwargs)
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
    -- currently occupies the row. See bot/merge.py.
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
# Empty for orders that exist in one system only. Feeds merge_key (bot/merge.py).
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
# _connect(transactional=True) plus the DROP below are what make the comment
# true. Verified by killing the process mid-copy and restarting it.
#
# The previous mechanism was a list of ALTER statements under `except: pass`.
# It could not express this change (SQLite cannot alter a UNIQUE constraint),
# and it silently swallowed real failures — a full disk logged success.

SCHEMA_VERSION = 6


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


# (version, name, coroutine). Append only; never edit one that has shipped.
_MIGRATIONS: tuple[tuple[int, str, object], ...] = (
    (1, "late columns", _migration_1_late_columns),
    (2, "orders owned per chat", _migration_2_orders_owned_per_chat),
    (3, "merge_key identity", _migration_3_merge_key),
    (4, "support threads", _migration_4_support_threads),
    (5, "persistent fsm state", _migration_5_fsm_state),
    (6, "support albums", _migration_6_support_albums),
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
    async with _connect() as db:
        await db.execute("PRAGMA journal_mode = WAL")

    # One transaction for the whole schema: tables, migrations, indexes and the
    # version stamp commit together or not at all.
    async with _connect(transactional=True) as db:
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
    "chat_id", "merge_key", "source", "source_order_id", "source_rank",
    "external_id", "order_name",
    "status_name", "status_group_id", "grand_total", "currency", "ordered_at",
    "products_json",
    "buyer_name", "payment_status", "tracking_code", "shipping_status",
    "delivery_city", "receive_point", "recipient_name",
)

# On conflict, whose value wins.
#   _PRIORITY  — the reporting system's own view of the order. Overwritten only
#                by a source at least as authoritative (KeyCRM over Shopify).
#   _KEEP_BEST — a value one source has and the other does not; a non-empty
#                incoming value fills a gap, an empty one never erases.
_PRIORITY = ("source", "source_order_id", "status_name", "status_group_id",
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

    Written out rather than hand-typed because it is twenty near-identical CASE
    expressions and a single wrong column name would silently stop one field
    from ever updating.

    GREATEST is not used: SQLite has no such function and PostgreSQL's max() is
    an aggregate, so CASE WHEN is the only form that survives the move to
    Postgres unchanged. Verified on sqlite 3.51.2.
    """
    cols = ", ".join(_ORDER_COLUMNS)
    placeholders = ", ".join("?" for _ in _ORDER_COLUMNS)
    wins = "excluded.source_rank >= orders.source_rank"
    sets = [f"{c} = CASE WHEN {wins} THEN excluded.{c} ELSE orders.{c} END"
            for c in _PRIORITY]
    sets += [f"{c} = CASE WHEN excluded.{c} != '' THEN excluded.{c} ELSE orders.{c} END"
             for c in _KEEP_BEST]
    sets.append("source_rank = CASE WHEN excluded.source_rank > orders.source_rank "
                "THEN excluded.source_rank ELSE orders.source_rank END")
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
    async with _connect() as db:
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


# Support threads older than this are never replied to in practice, and the
# table would otherwise grow for the life of the bot.
_SUPPORT_THREAD_TTL_DAYS = 90


# A conversation nobody has touched for this long is abandoned. Without a sweep
# the table would keep a row for every person who ever opened a flow and walked
# away, and they would still be in it a year later.
_FSM_TTL_DAYS = 7


async def fsm_save(key: str, *, state: str | None = ..., data: str | None = ...) -> None:
    """Write state, data, or both. Anything not passed is left alone."""
    sets, params = [], []
    if state is not ...:
        sets.append("state = excluded.state")
        params.append(state)
    else:
        params.append(None)
    if data is not ...:
        sets.append("data = excluded.data")
        params.append(data)
    else:
        params.append("{}")
    sets.append("updated_at = datetime('now')")
    async with _connect() as db:
        await db.execute(
            "INSERT INTO fsm_state (key, state, data) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET " + ", ".join(sets),
            (key, *params),
        )
        await db.execute(
            "DELETE FROM fsm_state WHERE updated_at < datetime('now', ?)",
            (f"-{_FSM_TTL_DAYS} days",),
        )
        await db.commit()


async def fsm_load(key: str) -> tuple[str | None, str | None]:
    """(state, data) for a conversation, (None, None) if it has none."""
    async with _connect() as db:
        cursor = await db.execute(
            "SELECT state, data FROM fsm_state WHERE key = ?", (key,))
        row = await cursor.fetchone()
        return (row[0], row[1]) if row else (None, None)


async def fsm_delete(key: str) -> None:
    async with _connect() as db:
        await db.execute("DELETE FROM fsm_state WHERE key = ?", (key,))
        await db.commit()


async def remember_support_thread(admin_message_ids: list[int], chat_id: int) -> None:
    """Record which customer a set of support-chat messages belongs to."""
    if not admin_message_ids:
        return
    async with _connect() as db:
        await db.executemany(
            "INSERT OR REPLACE INTO support_threads (admin_message_id, chat_id) "
            "VALUES (?, ?)",
            [(mid, chat_id) for mid in admin_message_ids],
        )
        await db.execute(
            "DELETE FROM support_threads WHERE created_at < datetime('now', ?)",
            (f"-{_SUPPORT_THREAD_TTL_DAYS} days",),
        )
        await db.commit()


# An album's parts arrive within a second of each other. An hour is generous and
# keeps the table from holding anything meaningful for long.
_ALBUM_TTL_MINUTES = 60


async def start_album(chat_id: int, media_group_id: str) -> bool:
    """Claim an album for this chat. True if this is its first message.

    The insert is the claim: a second caller for the same album conflicts and
    gets False, so only one message of an album triggers the metadata line and
    the confirmation to the customer.
    """
    async with _connect() as db:
        cursor = await db.execute(
            "INSERT OR IGNORE INTO support_albums (chat_id, media_group_id) "
            "VALUES (?, ?)",
            (chat_id, media_group_id),
        )
        first = cursor.rowcount == 1
        await db.execute(
            "DELETE FROM support_albums WHERE created_at < datetime('now', ?)",
            (f"-{_ALBUM_TTL_MINUTES} minutes",),
        )
        await db.commit()
        return first


async def album_in_progress(chat_id: int, media_group_id: str) -> bool:
    async with _connect() as db:
        cursor = await db.execute(
            "SELECT 1 FROM support_albums WHERE chat_id = ? AND media_group_id = ?",
            (chat_id, media_group_id),
        )
        return await cursor.fetchone() is not None


async def support_thread_owner(admin_message_id: int) -> int | None:
    """The customer behind a message in the support chat, or None if unknown."""
    async with _connect() as db:
        cursor = await db.execute(
            "SELECT chat_id FROM support_threads WHERE admin_message_id = ?",
            (admin_message_id,),
        )
        row = await cursor.fetchone()
        return row[0] if row else None


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
