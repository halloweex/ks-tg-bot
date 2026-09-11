"""Product analytics: one append-only table and the questions asked of it.

chat_id is stored deliberately — without it there are no cohorts and no way to
tell a returning user from a new one. Writes go through bot.analytics.track(),
never straight from a handler, so a slow disk cannot stall a screen.
"""
from __future__ import annotations

from collections.abc import Sequence

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


async def last_seen(event: str) -> str | None:
    """When `event` was last recorded, or None if it never has been.

    For watching a channel that is supposed to be noisy. The orders sync has
    `sync_state.last_success_at` for the same job, but that table is built
    around a cursor — how far a *reading* sweep has got — and a webhook is
    pushed to rather than read from, so its row there would carry a cursor
    column that could never mean anything.

    A string, not a datetime, for the reason the rest of this module returns
    counts: the spelling belongs to whoever stores it. The caller compares it
    against `datetime('now')` through the same SQLite that wrote it.
    """
    async with connect() as db:
        cursor = await db.execute(
            "SELECT MAX(created_at) FROM events WHERE event = ?", (event,))
        row = await cursor.fetchone()
        return row[0] if row else None


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


async def funnel_counts(days: int, steps: Sequence[str]) -> dict[str, int]:
    """Distinct users reaching each of the named steps.

    The steps arrive from the caller rather than living here. They used to be
    written down twice — once in this tuple and once as FUNNEL_STEPS in the
    scenario — and the two lists were equal only because nobody had edited one
    of them. Which steps make a funnel is a question about the report, and the
    report is now the only place that answers it.
    """
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


class SqliteUsageStats:
    """Implements core.ports.analytics.UsageStats against today's database.

    A delegating shell over the functions above, in the same shape
    core/repos/uow.py uses for the repositories: the seam moves now, while
    SQLite is underneath and every query is unchanged, and the engine can be
    swapped afterwards without the scenario being touched a second time.

    Not on the UnitOfWork, and not a mistake — see the port. These are four
    reads that belong to no user, and a transaction has nothing to offer them.
    """

    async def users_reaching(
        self, steps: Sequence[str], *, days: int
    ) -> dict[str, int]:
        return await funnel_counts(days, steps)

    async def order_lookup_misses(self, *, days: int) -> tuple[int, int]:
        return await lookup_miss_rate(days)

    async def retention(self, *, days: int) -> tuple[int, int]:
        return await returning_users(days)

    async def busiest_events(self, *, days: int) -> list[tuple[str, int, int]]:
        return await event_counts(days)
