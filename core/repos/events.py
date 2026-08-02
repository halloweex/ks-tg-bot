"""Product analytics: one append-only table and the questions asked of it.

chat_id is stored deliberately — without it there are no cohorts and no way to
tell a returning user from a new one. Writes go through bot.analytics.track(),
never straight from a handler, so a slow disk cannot stall a screen.
"""
from __future__ import annotations

from core.repos.base import connect


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
