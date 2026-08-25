"""Who brought whom, and whether it has been paid for.

Two halves in two places, on purpose. `users.source` is the first touch — which
link a person arrived through — written once at registration and never
rewritten, because "who brought them" does not change. This table is the other
half: the reward, which is owed exactly once and whose sweep runs every quarter
of an hour, so "have we already done this" has to be a row and not a guess.

Keyed by the friend for the same reason `users.source` is: a person is brought
by one link, the first one they opened.
"""
from __future__ import annotations

import aiosqlite

from core.domain.referral import Earned
from core.repos.base import connect
from core.repos.orders import CANCELLED_STATUS_GROUP


async def earned_referrals(prefix: str, limit: int = 50) -> list[tuple[int, int]]:
    """Friends who arrived through a link and have since ordered, unrewarded.

    The condition the owner chose: a friend counts once she has an order that
    was not cancelled. Registration alone is not enough — it costs nothing to
    open a bot, and a programme that pays for that pays for nothing.

    Returns (friend_chat_id, referrer_chat_id). The referrer is parsed out of
    the source here rather than stored twice: `users.source` is the record, and
    a second copy of it is a second thing to keep true.
    """
    async with connect() as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT u.chat_id AS friend, "
            "       CAST(substr(u.source, ?) AS INTEGER) AS referrer "
            "  FROM users u "
            " WHERE u.source LIKE ? || '%' "
            "   AND CAST(substr(u.source, ?) AS INTEGER) != 0 "
            "   AND CAST(substr(u.source, ?) AS INTEGER) != u.chat_id "
            "   AND NOT EXISTS (SELECT 1 FROM referrals r "
            "                    WHERE r.friend_chat_id = u.chat_id) "
            "   AND EXISTS (SELECT 1 FROM orders o "
            "                WHERE o.chat_id = u.chat_id "
            "                  AND o.status_group_id != ?) "
            " LIMIT ?",
            (len(prefix) + 1, prefix, len(prefix) + 1, len(prefix) + 1,
             CANCELLED_STATUS_GROUP, limit),
        )
        return [(row["friend"], row["referrer"]) for row in await cursor.fetchall()]


async def record_reward(friend_chat_id: int, referrer_chat_id: int,
                        code: str = "") -> bool:
    """Write that this referral has been paid for. False if it already was.

    The whole point of the table: the sweep runs every fifteen minutes and this
    is what stops the second run owing the reward again.
    """
    async with connect() as db:
        cursor = await db.execute(
            "INSERT OR IGNORE INTO referrals (friend_chat_id, referrer_chat_id, code) "
            "VALUES (?, ?, ?)",
            (friend_chat_id, referrer_chat_id, code),
        )
        await db.commit()
        return bool(cursor.rowcount)


async def referral_counts(referrer_chat_id: int, prefix: str) -> tuple[int, int]:
    """How many this person invited, and how many of those have ordered.

    Two numbers because they are two different states and a screen that shows
    only the second one looks broken to somebody whose friend has not ordered
    yet.
    """
    async with connect() as db:
        cursor = await db.execute(
            "SELECT COUNT(*) FROM users WHERE source = ?",
            (f"{prefix}{referrer_chat_id}",),
        )
        invited = (await cursor.fetchone())[0]
        cursor = await db.execute(
            "SELECT COUNT(*) FROM referrals WHERE referrer_chat_id = ?",
            (referrer_chat_id,),
        )
        return invited, (await cursor.fetchone())[0]


async def set_reward_code(friend_chat_id: int, code: str) -> None:
    """Record which code paid for this referral.

    Written after the row exists, not with it: the row is what stops a second
    payment and has to be there before anything is sent, while the code is what
    was actually handed over and is worth keeping for whoever asks later.
    """
    async with connect() as db:
        await db.execute("UPDATE referrals SET code = ? WHERE friend_chat_id = ?",
                         (code, friend_chat_id))
        await db.commit()


class SqliteReferralLedger:
    """Implements core.ports.repositories.ReferralLedger against today's tables.

    Three of the four functions above, and the missing one is the point:
    `referral_counts` belongs to the invite screen, not to the sweep. A sweep
    holding a handle that can count somebody's invitations is surface added
    before it has a caller, and the same reasoning kept subscribing out of
    RestockWatchlist.

    The two writes keep their order and their separateness — the seam moves,
    the queries do not.
    """

    async def earned(self, prefix: str, limit: int) -> list[Earned]:
        return [Earned(friend_chat_id=friend, referrer_chat_id=referrer)
                for friend, referrer in await earned_referrals(prefix, limit)]

    async def record_reward(
        self, friend_chat_id: int, referrer_chat_id: int
    ) -> bool:
        return await record_reward(friend_chat_id, referrer_chat_id)

    async def record_code(self, friend_chat_id: int, code: str) -> None:
        await set_reward_code(friend_chat_id, code)
