"""The users table and the opt-out list beside it.

One aggregate: everything keyed by chat_id that says who a person is and whether
they may be written to. get_broadcast_recipients lives here rather than in
broadcast.py because the question it answers — who has not opted out — is about
users, and the broadcast repository only records what happened to a job.
"""
from __future__ import annotations

import aiosqlite

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


async def registered_phones() -> list[tuple[int, str]]:
    """Every chat and the number bound to it, as stored.

    The sweep in core.usecases.sync_incremental reads what the CRM changed and
    then has to work out whose it is, which is the opposite direction from every
    other query here: not "the number for this chat" but "the chats for these
    numbers". Answered in Python rather than by a SQL join on the number,
    because the two sides are written by different systems and only agree after
    normalization — which is domain logic and has no business being expressed
    twice, once here as string functions.

    The whole table, deliberately. It is one row per registered customer, read
    once every two minutes; at the twenty thousand this business would have if
    every buyer signed up, it is still a scan of a narrow table against a
    request that just cost a second of network.
    """
    async with connect() as db:
        cursor = await db.execute(
            "SELECT chat_id, phone FROM users WHERE phone != ''"
        )
        return [(row[0], row[1]) for row in await cursor.fetchall()]


async def remember_crm_buyers(chat_id: int, buyer_ids: set[str]) -> None:
    """Record which CRM buyer cards this chat turned out to be.

    Written by the paths that ask the CRM by number and therefore already know
    the answer: registration, the orders screen's refresh, and the sweep's own
    resolution of a chat it does not recognise. Additive — a card seen once is
    kept, because the CRM's own search keeps returning its orders and dropping
    it here would make them disappear from the sweep alone.
    """
    ids = {str(buyer_id) for buyer_id in buyer_ids if str(buyer_id)}
    if not ids:
        return
    async with connect() as db:
        await db.executemany(
            "INSERT OR IGNORE INTO user_crm_buyers (chat_id, buyer_id) VALUES (?, ?)",
            [(chat_id, buyer_id) for buyer_id in sorted(ids)],
        )
        await db.commit()


async def registered_buyers() -> list[tuple[int, str]]:
    """Every chat and the CRM buyer cards it is known to be."""
    async with connect() as db:
        cursor = await db.execute("SELECT chat_id, buyer_id FROM user_crm_buyers")
        return [(row[0], row[1]) for row in await cursor.fetchall()]


async def chats_without_crm_buyer() -> list[tuple[int, str]]:
    """Registered chats whose CRM identity is not known yet, and their numbers.

    These are the ones the sweep cannot route: it sees orders by buyer card, and
    for them there is no card on file. Everyone registered before the map
    existed is on this list until something asks the CRM by their number.
    """
    async with connect() as db:
        cursor = await db.execute(
            "SELECT u.chat_id, u.phone FROM users u "
            " WHERE u.phone != '' "
            "   AND NOT EXISTS (SELECT 1 FROM user_crm_buyers b "
            "                    WHERE b.chat_id = u.chat_id)"
        )
        return [(row[0], row[1]) for row in await cursor.fetchall()]


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
