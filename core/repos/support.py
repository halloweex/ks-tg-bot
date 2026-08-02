"""Support: which customer a message in the support chat belongs to.

Also the discount requests. They are not a support thread, but they are the same
shape of thing — a customer asking for something a human answers in that same
chat — and a two-function module of their own would say less than this sentence.
"""
from __future__ import annotations

from core.repos.base import connect


# Support threads older than this are never replied to in practice, and the
# table would otherwise grow for the life of the bot.
_SUPPORT_THREAD_TTL_DAYS = 90


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
