"""Queries the bot runs against SQLite.

Being taken apart aggregate by aggregate into core/repos/ — see
docs/move-status.md. The connection lives in core/repos/base.py and the schema
in core/repos/schema.py; what is left here is the queries themselves.
"""
from __future__ import annotations

import aiosqlite

from core.repos.base import connect


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
