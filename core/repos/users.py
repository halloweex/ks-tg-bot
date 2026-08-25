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
    source: str = "",
) -> None:
    """Persist a verified chat_id-to-phone mapping with optional profile fields.

    Uses INSERT OR REPLACE — chat_id is PRIMARY KEY, so re-verification
    overwrites the old row. Optional full_name/email are stored when provided.

    REPLACE writes a whole new row, so **any column not listed here is reset to
    its default** — which is why every one of them is carried over by hand
    below. Losing the language would flip the customer back to Ukrainian;
    losing created_at would destroy the signup cohort; losing the birthday
    would mean asking Telegram for it again and, until the next sweep, no
    greeting; losing crm_checked_at would put the chat back in the queue of
    "never looked up".

    `source` is the deep link this person arrived through, and it is written
    **once**: an empty column takes the new value, a filled one keeps what it
    has. First touch is the question it answers — who brought them — and a
    later visit through somebody else's link does not change that.
    """
    async with connect() as db:
        await db.execute(
            "INSERT OR REPLACE INTO users "
            "(chat_id, phone, full_name, email, language, source, crm_checked_at, "
            " birthdate, birthdate_checked_at, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, "
            "  (SELECT language FROM users WHERE chat_id = ?), "
            "  COALESCE(NULLIF((SELECT source FROM users WHERE chat_id = ?), ''), ?), "
            "  (SELECT crm_checked_at FROM users WHERE chat_id = ?), "
            "  (SELECT birthdate FROM users WHERE chat_id = ?), "
            "  (SELECT birthdate_checked_at FROM users WHERE chat_id = ?), "
            "  COALESCE((SELECT created_at FROM users WHERE chat_id = ?), datetime('now')), "
            "  datetime('now'))",
            (chat_id, phone, full_name, email, chat_id, chat_id, source,
             chat_id, chat_id, chat_id, chat_id),
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


async def chats_without_crm_buyer(*, retry_after_hours: int = 24) -> list[tuple[int, str]]:
    """Registered chats whose CRM identity is not known yet, and their numbers.

    These are the ones the sweep cannot route: it sees orders by buyer card, and
    for them there is no card on file. Everyone registered before the map
    existed is on this list until something asks the CRM by their number.

    Asked-and-found-nothing counts as asked, for a day. A customer who has never
    ordered has no card to find, so without the timestamp they would be looked
    up on every sweep for as long as they stay registered — two of the three
    people in production are exactly that. A day later they are asked again,
    because "has never ordered" is a state customers leave.
    """
    async with connect() as db:
        cursor = await db.execute(
            "SELECT u.chat_id, u.phone FROM users u "
            " WHERE u.phone != '' "
            "   AND NOT EXISTS (SELECT 1 FROM user_crm_buyers b "
            "                    WHERE b.chat_id = u.chat_id) "
            f"   AND (u.crm_checked_at IS NULL "
            f"        OR u.crm_checked_at < datetime('now', '-{int(retry_after_hours)} hours'))"
        )
        return [(row[0], row[1]) for row in await cursor.fetchall()]


async def mark_crm_checked(chat_id: int) -> None:
    """Record that the CRM was asked about this chat, whatever it answered."""
    async with connect() as db:
        await db.execute(
            "UPDATE users SET crm_checked_at = datetime('now') WHERE chat_id = ?",
            (chat_id,),
        )
        await db.commit()


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


# --------------------------------------------------------------------------
# Birthdays
# --------------------------------------------------------------------------
#
# Telegram is the only source: `getChat` carries a `birthdate` for a private
# chat when the person has set one and their privacy lets a bot see it. Most
# have not, so the column is empty for most people and the greeting simply does
# not happen for them — see core/usecases/birthdays.py.


async def save_birthday(chat_id: int, birthdate: str) -> None:
    """Record "MM-DD", or "" for a customer Telegram shows us no date for.

    Both stamp `birthdate_checked_at`, which is what stops the sweep asking the
    same people every hour: an answer of "there is none" is an answer.
    """
    async with connect() as db:
        await db.execute(
            "UPDATE users SET birthdate = ?, birthdate_checked_at = datetime('now') "
            "WHERE chat_id = ?",
            (birthdate, chat_id),
        )
        await db.commit()


async def chats_without_birthday(limit: int, *, stale_days: int = 90) -> list[int]:
    """Customers to ask Telegram about, never-asked ones first.

    Re-asked every few months rather than once, because a birthday is something
    people fill in long after they sign up — and the cost of asking is one API
    call for somebody the bot already talks to.
    """
    async with connect() as db:
        cursor = await db.execute(
            "SELECT chat_id FROM users "
            " WHERE birthdate_checked_at IS NULL "
            "    OR birthdate_checked_at < datetime('now', ?) "
            " ORDER BY birthdate_checked_at IS NOT NULL, birthdate_checked_at "
            " LIMIT ?",
            (f"-{stale_days} days", limit),
        )
        return [row[0] for row in await cursor.fetchall()]


async def chats_with_birthday_on(month_day: str) -> list[int]:
    """Everyone whose birthday is "MM-DD", opt-outs excluded.

    The opt-out check belongs here rather than in the caller for the same
    reason get_broadcast_recipients does: "may this person be written to" is a
    question about users, and a greeting is still something the bot decided to
    send.
    """
    async with connect() as db:
        cursor = await db.execute(
            "SELECT u.chat_id FROM users u "
            " LEFT JOIN opt_out o ON o.chat_id = u.chat_id "
            " WHERE u.birthdate = ? AND o.chat_id IS NULL",
            (month_day,),
        )
        return [row[0] for row in await cursor.fetchall()]


async def get_source(chat_id: int) -> str:
    """The deep link this customer arrived through, or "" if they came alone."""
    async with connect() as db:
        cursor = await db.execute(
            "SELECT source FROM users WHERE chat_id = ?", (chat_id,))
        row = await cursor.fetchone()
        return (row[0] or "") if row else ""


class SqliteLanguageChoice:
    """Implements core.ports.users.LanguageChoice against today's table."""

    async def chosen_by(self, chat_id: int) -> str | None:
        return await get_user_language(chat_id)


class SqliteMailingList:
    """Implements core.ports.users.MailingList against today's tables.

    Two tables, in fact — `users` and `opt_out` — which is part of why the pair
    belongs behind one port: the question and the write that changes its answer
    are one rule, and a caller holding two objects would be a caller who can
    hold two that disagree.
    """

    async def recipients(self) -> list[int]:
        return await get_broadcast_recipients()

    async def opt_out(self, chat_id: int) -> None:
        await opt_out_user(chat_id)


class SqliteKnownBirthdays:
    """Implements core.ports.users.KnownBirthdays against today's columns.

    `stale_days` arrives from the caller rather than defaulting here. The
    function below still carries a default for its own callers and its own
    tests; what the port refuses is a second place where the sweep's cost is
    decided.
    """

    async def to_ask(self, limit: int, *, stale_days: int) -> list[int]:
        return await chats_without_birthday(limit, stale_days=stale_days)

    async def remember(self, chat_id: int, birthdate: str) -> None:
        await save_birthday(chat_id, birthdate)

    async def celebrating_on(self, month_day: str) -> list[int]:
        return await chats_with_birthday_on(month_day)
