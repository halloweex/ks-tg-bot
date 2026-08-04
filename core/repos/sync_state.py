"""How far the sync has read, and whether it is still reading.

One row per source, and every column on it answers a question somebody asks
during an incident.

**`cursor`** is the upper bound of the last window that was read to the end. It
moves after a whole sweep succeeded and at no other moment: a cursor advanced on
a partial read turns one transient error into orders nobody ever fetches again,
because the window that contained them is behind the cursor forever
(docs/architecture.md §5.2).

**`last_success_at`** is the column the alert reads, and §5.5 is explicit about
why it is not `last_error`: a scheduler that stopped, or a task that was quietly
cancelled, raises nothing at all. Monitoring that waits for an error sees a
healthy system whose data froze hours ago — which is the failure this bot is
most exposed to, since "the bot reads only from the database" means a stalled
sync is silently stale orders for everyone, with no fallback to the live API.

**`last_run_at`** is written when a sweep starts, not when it ends. That is what
separates "sweeps are happening and failing" from "nothing is running at all",
and the two have different causes and different fixes.

**`last_full_at`** is the sixth column, beyond the five §5.2 names, and records
when the weekly reconciliation (§5.3) last finished. The alternative was a
second row under its own source name, but "keycrm" and "keycrm:full" would be
two rows describing one integration, and everything that asks "is the data
moving" would then have to know which of the two to believe.

`last_error` holds an operational message for a human, so it is truncated and
never trusted to be short. It is not a log line: the sink that masks phone
numbers (bot/logs.py) does not reach a database column, which is why the only
caller passes errors from the changed-window request — a URL that carries a time
range and no customer.
"""
from __future__ import annotations

import aiosqlite

from core.repos.base import connect

# What fits in an alert message and a glance at the row. httpx errors carry the
# whole request URL and pydantic errors carry the whole model.
_MAX_ERROR_CHARS = 500


async def begin_run(source: str) -> None:
    """Record that a sweep started. Creates the row on first ever run."""
    async with connect() as db:
        await db.execute(
            "INSERT INTO sync_state (source, last_run_at) "
            "VALUES (?, datetime('now')) "
            "ON CONFLICT(source) DO UPDATE SET last_run_at = datetime('now')",
            (source,),
        )
        await db.commit()


async def finish_success(source: str, cursor: str, *, full: bool = False) -> None:
    """Move the cursor and clear the error: the whole window was read.

    `full` marks a weekly reconciliation, which covers its window regardless of
    where the cursor was and therefore also satisfies the incremental one.
    """
    async with connect() as db:
        await db.execute(
            "INSERT INTO sync_state "
            "(source, cursor, last_run_at, last_success_at, last_error, last_full_at) "
            "VALUES (?, ?, datetime('now'), datetime('now'), NULL, "
            "        CASE WHEN ? THEN datetime('now') ELSE NULL END) "
            "ON CONFLICT(source) DO UPDATE SET "
            "  cursor = excluded.cursor, "
            "  last_success_at = excluded.last_success_at, "
            "  last_error = NULL, "
            # COALESCE and not excluded.last_full_at: an incremental sweep must
            # leave the reconciliation's timestamp where it is, or the weekly
            # pass would be postponed by every successful two-minute run and
            # never happen again.
            "  last_full_at = COALESCE(excluded.last_full_at, sync_state.last_full_at)",
            (source, cursor, 1 if full else 0),
        )
        await db.commit()


async def finish_failure(source: str, error: str) -> None:
    """Record why a sweep did not finish, leaving the cursor where it was."""
    async with connect() as db:
        await db.execute(
            "INSERT INTO sync_state (source, last_run_at, last_error) "
            "VALUES (?, datetime('now'), ?) "
            "ON CONFLICT(source) DO UPDATE SET last_error = excluded.last_error",
            (source, error[:_MAX_ERROR_CHARS]),
        )
        await db.commit()


async def get_state(source: str) -> dict | None:
    """The whole row, or None if this source has never been swept."""
    async with connect() as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT source, cursor, last_run_at, last_success_at, last_error, "
            "       last_full_at "
            "  FROM sync_state WHERE source = ?",
            (source,),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None
