"""Where the database is and how a connection to it is opened.

The bottom of core.repos: every repository module goes through connect(), and
nothing else in the tree is allowed to open a database (contract
only-repos-touch-db). Schema and migrations are next door in schema.py.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

import aiosqlite

# Where the database lives. The default is for local runs; production passes a
# path on the mounted volume. Set once at startup from Settings — see
# configure() — rather than read from the environment here, so that the
# environment has exactly one reader in the whole tree.
DB_PATH = Path("bot_data.db")


def configure(path: str | Path) -> None:
    """Point the module at a database file. Call before init_db()."""
    global DB_PATH
    DB_PATH = Path(path)

# How long a connection waits on a locked DB before erroring (ms).
_BUSY_TIMEOUT_MS = 5000


@asynccontextmanager
async def connect(*, transactional: bool = False) -> AsyncIterator[aiosqlite.Connection]:
    """Open a connection with a busy timeout so concurrent writers wait
    instead of failing with 'database is locked' during activity bursts.

    WAL journal mode is enabled once in init_db() and persists in the DB file,
    so readers here never block the background order-refresh writers.

    transactional=True switches the connection out of sqlite3's legacy mode,
    where an implicit BEGIN is issued before DML but *not* before DDL, into
    PEP 249 semantics, where a single transaction covers both. Only the schema
    work in init_db() needs it — every other caller writes one statement at a
    time and legacy mode is what it has always run under.

    Two things behave differently on a transactional connection, both verified
    on python 3.14.2 / sqlite 3.51.2:
      - `PRAGMA journal_mode = WAL` raises "cannot change into wal mode from
        within a transaction" as the first statement, and silently reports
        'delete' after any other statement has opened one. WAL is therefore
        set on its own connection in init_db().
      - closing without commit rolls the whole thing back, which is the point.
    """
    kwargs = {"autocommit": False} if transactional else {}
    db = await aiosqlite.connect(DB_PATH, **kwargs)
    try:
        await db.execute(f"PRAGMA busy_timeout = {_BUSY_TIMEOUT_MS}")
        yield db
    finally:
        await db.close()
