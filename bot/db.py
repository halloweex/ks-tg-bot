"""Queries the bot runs against SQLite.

Being taken apart aggregate by aggregate into core/repos/ — see
docs/move-status.md. The connection lives in core/repos/base.py and the schema
in core/repos/schema.py; what is left here is the queries themselves.
"""
from __future__ import annotations

import aiosqlite

from core.domain.order import merge_key, source_rank
from core.repos.base import connect


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
    async with connect() as db:
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
    async with connect() as db:
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
    async with connect() as db:
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
    async with connect() as db:
        await db.execute(
            "UPDATE users SET language = ?, updated_at = datetime('now') "
            "WHERE chat_id = ?",
            (lang, chat_id),
        )
        await db.commit()


async def get_user(chat_id: int) -> dict | None:
    """Return full user profile dict or None if not found."""
    async with connect() as db:
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
    async with connect() as db:
        await db.execute(
            "INSERT OR IGNORE INTO opt_out (chat_id) VALUES (?)",
            (chat_id,),
        )
        await db.commit()


async def opt_in_user(chat_id: int) -> None:
    """Remove a user from the opt-out list (re-subscribe)."""
    async with connect() as db:
        await db.execute(
            "DELETE FROM opt_out WHERE chat_id = ?",
            (chat_id,),
        )
        await db.commit()


async def is_opted_out(chat_id: int) -> bool:
    """Check whether a user has opted out of broadcasts."""
    async with connect() as db:
        cursor = await db.execute(
            "SELECT 1 FROM opt_out WHERE chat_id = ?",
            (chat_id,),
        )
        return await cursor.fetchone() is not None


async def get_broadcast_recipients() -> list[int]:
    """Return chat_ids of all users who have NOT opted out."""
    async with connect() as db:
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
    async with connect() as db:
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
    async with connect() as db:
        cursor = await db.execute(
            "SELECT state, data FROM fsm_state WHERE key = ?", (key,))
        row = await cursor.fetchone()
        return (row[0], row[1]) if row else (None, None)


async def fsm_delete(key: str) -> None:
    async with connect() as db:
        await db.execute("DELETE FROM fsm_state WHERE key = ?", (key,))
        await db.commit()


async def remember_support_thread(admin_message_ids: list[int], chat_id: int) -> None:
    """Record which customer a set of support-chat messages belongs to."""
    if not admin_message_ids:
        return
    async with connect() as db:
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
    async with connect() as db:
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
    async with connect() as db:
        cursor = await db.execute(
            "SELECT 1 FROM support_albums WHERE chat_id = ? AND media_group_id = ?",
            (chat_id, media_group_id),
        )
        return await cursor.fetchone() is not None


async def support_thread_owner(admin_message_id: int) -> int | None:
    """The customer behind a message in the support chat, or None if unknown."""
    async with connect() as db:
        cursor = await db.execute(
            "SELECT chat_id FROM support_threads WHERE admin_message_id = ?",
            (admin_message_id,),
        )
        row = await cursor.fetchone()
        return row[0] if row else None


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


# ---------------------------------------------------------------------------
# Durable broadcast
# ---------------------------------------------------------------------------


async def create_broadcast_job(text: str, created_by: int) -> int:
    """Create a broadcast job and snapshot the current recipient list into
    broadcast_targets (all 'pending'). Returns the new job id.

    The recipient set is frozen at creation time so a later opt-out/opt-in
    can't change which people this job is responsible for.
    """
    async with connect() as db:
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
    async with connect() as db:
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
    async with connect() as db:
        await db.execute(
            "UPDATE broadcast_targets SET status = ?, error = ?, "
            "updated_at = datetime('now') WHERE job_id = ? AND chat_id = ?",
            (status, error, job_id, chat_id),
        )
        await db.commit()


async def get_unfinished_broadcasts() -> list[dict]:
    """Return jobs still marked 'running' (to resume after a restart)."""
    async with connect() as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT id, text, created_by FROM broadcast_jobs "
            "WHERE status = 'running' ORDER BY id"
        )
        return [dict(row) for row in await cursor.fetchall()]


async def finish_broadcast_job(job_id: int) -> None:
    """Mark a job done once all its targets are processed."""
    async with connect() as db:
        await db.execute(
            "UPDATE broadcast_jobs SET status = 'done', "
            "finished_at = datetime('now') WHERE id = ?",
            (job_id,),
        )
        await db.commit()


async def broadcast_job_stats(job_id: int) -> dict:
    """Return counts per status for a job: sent/failed/blocked/pending."""
    async with connect() as db:
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
    async with connect() as db:
        await db.execute(
            "INSERT INTO events (chat_id, event, meta) VALUES (?, ?, ?)",
            (chat_id, event, meta),
        )
        await db.commit()


async def event_counts(days: int = 7) -> list[tuple[str, int, int]]:
    """(event, occurrences, distinct users) over the last `days`."""
    async with connect() as db:
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
    async with connect() as db:
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
    async with connect() as db:
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
    async with connect() as db:
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


# ---------------------------------------------------------------------------
# Back-in-stock subscriptions
# ---------------------------------------------------------------------------


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


async def recent_discount_request(chat_id: int, days: int = 7) -> bool:
    """True if this customer already asked within the window.

    Stops a second tap, or a bored customer, from filling the support chat with
    the same request.
    """
    async with connect() as db:
        cursor = await db.execute(
            "SELECT 1 FROM discount_requests WHERE chat_id = ? "
            "AND created_at >= datetime('now', ?) LIMIT 1",
            (chat_id, f"-{days} days"),
        )
        return await cursor.fetchone() is not None


async def add_discount_request(chat_id: int, products_json: str) -> None:
    async with connect() as db:
        await db.execute(
            "INSERT INTO discount_requests (chat_id, products_json) VALUES (?, ?)",
            (chat_id, products_json),
        )
        await db.commit()
