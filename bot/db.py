"""SQLite database initialization and connection management."""
from __future__ import annotations

from pathlib import Path

import aiosqlite
from loguru import logger

DB_PATH = Path("bot_data.db")

_CREATE_USERS = """
CREATE TABLE IF NOT EXISTS users (
    chat_id    INTEGER PRIMARY KEY,
    phone      TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

_CREATE_OPT_OUT = """
CREATE TABLE IF NOT EXISTS opt_out (
    chat_id      INTEGER PRIMARY KEY,
    opted_out_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


async def init_db() -> None:
    """Create the database and initialize tables if they don't exist.

    Safe to call multiple times — uses IF NOT EXISTS.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(_CREATE_USERS)
        await db.execute(_CREATE_OPT_OUT)
        await db.commit()
    logger.info("Database initialized at {}", DB_PATH)


async def get_db() -> aiosqlite.Connection:
    """Open a connection. Caller must close it (use as async context manager)."""
    return await aiosqlite.connect(DB_PATH)


async def save_user(chat_id: int, phone: str) -> None:
    """Persist a verified chat_id-to-phone mapping.

    Uses INSERT OR REPLACE — chat_id is PRIMARY KEY, so re-verification
    overwrites the old phone number.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO users (chat_id, phone) VALUES (?, ?)",
            (chat_id, phone),
        )
        await db.commit()


async def get_user_phone(chat_id: int) -> str | None:
    """Return the phone number for a verified user, or None if not found."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT phone FROM users WHERE chat_id = ?",
            (chat_id,),
        )
        row = await cursor.fetchone()
        return row[0] if row else None
