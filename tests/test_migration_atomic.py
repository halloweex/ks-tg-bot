"""The schema migration survives a process kill halfway through.

Guards the failure that motivated commit 424f837: sqlite3's legacy transaction
mode commits DDL as it runs, so a crash during migration 2 left a partly filled
`orders_migrated`, the next start died on "table already exists", and
`restart: always` made that a crash loop with nothing to roll back to.
"""
from __future__ import annotations

import asyncio
import os
import sqlite3
import subprocess
import sys

import pytest

from tests.conftest import REPO_ROOT

# The pre-migration shape: unique key without chat_id, no external_id.
OLD_ORDERS = """
CREATE TABLE orders (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id         INTEGER NOT NULL REFERENCES users(chat_id),
    source          TEXT NOT NULL,
    source_order_id TEXT NOT NULL,
    external_id     TEXT DEFAULT '',
    order_name      TEXT DEFAULT '',
    status_name     TEXT DEFAULT '',
    grand_total     REAL DEFAULT 0,
    currency        TEXT DEFAULT 'грн',
    ordered_at      TEXT DEFAULT '',
    products_json   TEXT DEFAULT '[]',
    buyer_name      TEXT DEFAULT '',
    payment_status  TEXT DEFAULT '',
    tracking_code   TEXT DEFAULT '',
    shipping_status TEXT DEFAULT '',
    delivery_city   TEXT DEFAULT '',
    receive_point   TEXT DEFAULT '',
    recipient_name  TEXT DEFAULT '',
    synced_at       TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(source, source_order_id)
);
"""

# Runs init_db in a child process and hard-kills it the moment the copy starts.
CRASHER = r"""
import asyncio, os, sys
sys.path.insert(0, %r)
import aiosqlite
_orig = aiosqlite.Connection.execute
async def execute(self, sql, *a, **kw):
    if isinstance(sql, str) and sql.lstrip().startswith("INSERT INTO orders_migrated"):
        os._exit(9)
    return await _orig(self, sql, *a, **kw)
aiosqlite.Connection.execute = execute
from bot.db import init_db
asyncio.run(init_db())
"""


def _build_v1(path: str) -> None:
    db = sqlite3.connect(path)
    db.executescript(
        "CREATE TABLE users (chat_id INTEGER PRIMARY KEY, phone TEXT NOT NULL,"
        " created_at TEXT NOT NULL DEFAULT (datetime('now')), full_name TEXT,"
        " email TEXT, updated_at TEXT, language TEXT);" + OLD_ORDERS
    )
    db.execute("INSERT INTO users (chat_id, phone) VALUES (1, '380670000001')")
    for i in range(1, 26):
        db.execute(
            "INSERT INTO orders (id, chat_id, source, source_order_id, order_name)"
            " VALUES (?, 1, 'keycrm', ?, ?)",
            (i, str(1000 + i), f"#{i}"),
        )
    db.execute("PRAGMA user_version = 1")
    db.commit()
    db.close()


def _probe(path: str) -> dict:
    db = sqlite3.connect(path)
    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    out = {
        "user_version": db.execute("PRAGMA user_version").fetchone()[0],
        "orders": db.execute("SELECT COUNT(*) FROM orders").fetchone()[0],
        "ids": [r[0] for r in db.execute("SELECT id FROM orders ORDER BY id")],
        "leftover": "orders_migrated" in tables,
        "journal_mode": db.execute("PRAGMA journal_mode").fetchone()[0],
        "unique_sql": db.execute(
            "SELECT sql FROM sqlite_master WHERE name='orders'"
        ).fetchone()[0].split("UNIQUE")[-1].strip(" ();\n"),
    }
    db.close()
    return out


@pytest.fixture()
def v1_db(tmp_path):
    path = str(tmp_path / "bot_data.db")
    _build_v1(path)
    return path


def test_kill_mid_copy_rolls_back_completely(v1_db):
    before = _probe(v1_db)
    assert (before["user_version"], before["orders"]) == (1, 25)

    result = subprocess.run(
        [sys.executable, "-c", CRASHER % str(REPO_ROOT)],
        env=dict(os.environ, BOT_DB_PATH=v1_db),
        capture_output=True,
        text=True,
    )
    assert result.returncode in (9, -9), result.stderr

    after = _probe(v1_db)
    assert after["user_version"] == 1, "version moved despite the crash"
    assert after["orders"] == 25
    assert after["ids"] == before["ids"], "row ids are what expand buttons carry"
    assert not after["leftover"], "a partial orders_migrated would wedge the next start"


def test_restart_after_crash_migrates_unattended(v1_db):
    subprocess.run(
        [sys.executable, "-c", CRASHER % str(REPO_ROOT)],
        env=dict(os.environ, BOT_DB_PATH=v1_db),
        capture_output=True,
        text=True,
    )
    from bot import db as botdb

    botdb.DB_PATH = v1_db
    asyncio.run(botdb.init_db())

    after = _probe(v1_db)
    assert after["user_version"] == 2
    assert after["orders"] == 25
    assert not after["leftover"]
    assert "chat_id" in after["unique_sql"]


def test_wal_survives_the_transactional_block(v1_db):
    """WAL cannot be entered from inside a transaction.

    SQLite raises "cannot change into wal mode from within a transaction" as the
    first statement and silently reports 'delete' after any other statement has
    opened one. So moving `PRAGMA journal_mode = WAL` into the schema block
    disables WAL with no error anywhere — the regression this pins is a silent
    one.
    """
    from bot import db as botdb

    botdb.DB_PATH = v1_db
    asyncio.run(botdb.init_db())
    assert _probe(v1_db)["journal_mode"] == "wal"


def test_init_db_is_idempotent(v1_db):
    from bot import db as botdb

    botdb.DB_PATH = v1_db
    asyncio.run(botdb.init_db())
    asyncio.run(botdb.init_db())
    after = _probe(v1_db)
    assert after["user_version"] == 2
    assert after["orders"] == 25
    assert after["journal_mode"] == "wal"
