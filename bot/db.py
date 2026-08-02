"""Queries the bot runs against SQLite.

Being taken apart aggregate by aggregate into core/repos/ — see
docs/move-status.md. The connection lives in core/repos/base.py and the schema
in core/repos/schema.py; what is left here is the queries themselves.
"""
from __future__ import annotations

import aiosqlite

from core.repos.base import connect


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
