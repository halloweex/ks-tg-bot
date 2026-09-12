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


async def remember_support_thread(admin_message_ids: list[int], chat_id: int,
                                  admin_chat_id: int) -> None:
    """Record which customer a set of messages in one chat belongs to.

    `admin_chat_id` is which chat they were put in, and it is half the identity:
    Telegram numbers messages per chat, so the manager's message 1001 and an
    admin's message 1001 are different messages. Keyed on the id alone, the
    second one overwrote the first and a reply reached a stranger.
    """
    if not admin_message_ids:
        return
    async with connect() as db:
        await db.executemany(
            "INSERT OR REPLACE INTO support_threads "
            "  (admin_chat_id, admin_message_id, chat_id) VALUES (?, ?, ?)",
            [(admin_chat_id, mid, chat_id) for mid in admin_message_ids],
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


async def support_thread_owner(admin_message_id: int,
                               admin_chat_id: int) -> int | None:
    """The customer behind a message in one chat, or None if unknown.

    The chat is matched as well as the id, which is what stops a reply in one
    chat from being routed by a thread that belongs to another.

    `admin_chat_id = 0` is the exception and it is deliberate: rows written
    before the column existed cannot say which chat they came from, so they
    still answer from any of them — exactly the behaviour they had. An exact
    match wins over a legacy one, and the legacy rows disappear with the
    90-day sweep.
    """
    async with connect() as db:
        cursor = await db.execute(
            "SELECT chat_id FROM support_threads "
            " WHERE admin_message_id = ? AND admin_chat_id IN (?, 0) "
            " ORDER BY admin_chat_id != 0 DESC LIMIT 1",
            (admin_message_id, admin_chat_id),
        )
        row = await cursor.fetchone()
        return row[0] if row else None


# How many unanswered asks one customer may have at once. Per-product asks
# removed the old "one a week, about anything" rule, and something has to keep
# a bored thumb from walking down a fifty-row list.
PENDING_LIMIT = 5


async def pending_discount_request(chat_id: int, sku: str = "",
                                   days: int = 7) -> bool:
    """True while this exact ask is still with a manager.

    Scoped to the product, because asking about a cream says nothing about a
    shampoo. `sku` empty means the whole favourites list, which is its own
    scope for the same reason.

    An answered request stops blocking immediately: the point of the window was
    never to ration questions, only to keep the same one from arriving twice.
    """
    async with connect() as db:
        cursor = await db.execute(
            "SELECT 1 FROM discount_requests "
            " WHERE chat_id = ? AND sku = ? AND answered_at IS NULL "
            "   AND created_at >= datetime('now', ?) LIMIT 1",
            (chat_id, sku, f"-{days} days"),
        )
        return await cursor.fetchone() is not None


async def pending_discount_count(chat_id: int, days: int = 7) -> int:
    """How many of this customer's asks are still waiting for an answer."""
    async with connect() as db:
        cursor = await db.execute(
            "SELECT COUNT(*) FROM discount_requests "
            " WHERE chat_id = ? AND answered_at IS NULL "
            "   AND created_at >= datetime('now', ?)",
            (chat_id, f"-{days} days"),
        )
        return (await cursor.fetchone())[0]


async def pending_discount_skus(chat_id: int, days: int = 7) -> set[str]:
    """Every product this customer has an unanswered ask about.

    One query for a panel of fifty rows: asking per row would be fifty.
    """
    async with connect() as db:
        cursor = await db.execute(
            "SELECT sku FROM discount_requests "
            " WHERE chat_id = ? AND answered_at IS NULL "
            "   AND created_at >= datetime('now', ?)",
            (chat_id, f"-{days} days"),
        )
        return {row[0] for row in await cursor.fetchall()}


async def add_discount_request(chat_id: int, products_json: str, *,
                               sku: str = "", thread_message_id: int = 0) -> None:
    async with connect() as db:
        await db.execute(
            "INSERT INTO discount_requests "
            "(chat_id, products_json, sku, thread_message_id) VALUES (?, ?, ?, ?)",
            (chat_id, products_json, sku, thread_message_id),
        )
        await db.commit()


async def mark_discount_answered(message_id: int) -> None:
    """Close the ask a manager just replied to.

    The reply routes by the same message id the support relay routes by, so
    this needs no new plumbing in the support chat: whatever the manager
    replied to, if it was a discount ask, it stops being pending.
    """
    async with connect() as db:
        await db.execute(
            "UPDATE discount_requests SET answered_at = datetime('now') "
            " WHERE thread_message_id = ? AND answered_at IS NULL",
            (message_id,),
        )
        await db.commit()
