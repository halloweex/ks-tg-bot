"""The bot assembles and the schema builds. Cheapest possible regression net.

Deliberately does not start polling: that needs a live token and would talk to
Telegram. What it does check is everything up to that point, which is where an
import cycle, a missing router or a broken migration would surface.
"""
from __future__ import annotations

import asyncio
import inspect
import importlib
import os
import pkgutil
import sqlite3

import pytest

import bot as bot_pkg
from bot import db as botdb


def test_every_module_imports():
    """Also the cheapest cycle detector: a cycle raises here."""
    failed = []
    for mod in pkgutil.walk_packages(bot_pkg.__path__, prefix="bot."):
        if mod.name.endswith("__main__"):
            continue  # imported separately below, it has side-effect-free top level
        try:
            importlib.import_module(mod.name)
        except Exception as exc:  # noqa: BLE001
            failed.append((mod.name, exc))
    assert not failed, failed


def test_entrypoint_imports_and_exposes_main():
    module = importlib.import_module("bot.__main__")
    assert inspect.iscoroutinefunction(module.main)


def test_all_routers_are_registered():
    """Nine router modules, nine include_router calls (bot/__main__.py).

    delivery.py deliberately has no router — it is a screen called from menu.py.
    """

    from aiogram import Router

    from bot import __main__ as entry

    source = inspect.getsource(entry.main)
    registered = source.count("dp.include_router(")

    with_router = []
    for mod in pkgutil.iter_modules([str(next(iter(bot_pkg.__path__)) + "/handlers")]):
        module = importlib.import_module(f"bot.handlers.{mod.name}")
        if any(isinstance(v, Router) for v in vars(module).values()):
            with_router.append(mod.name)

    assert len(with_router) == registered, (
        f"{len(with_router)} modules define a Router, {registered} are registered: "
        f"{sorted(with_router)}"
    )
    assert "delivery" not in with_router


def test_schema_builds_on_an_empty_database(tmp_path, monkeypatch):
    path = tmp_path / "fresh.db"
    monkeypatch.setattr(botdb, "DB_PATH", str(path))
    asyncio.run(botdb.init_db())

    db = sqlite3.connect(path)
    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"users", "opt_out", "orders", "broadcast_jobs", "broadcast_targets",
            "events", "stock_levels", "stock_subscriptions", "discount_requests"} <= tables
    assert db.execute("PRAGMA user_version").fetchone()[0] == botdb.SCHEMA_VERSION
    assert db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    db.close()


@pytest.mark.skipif(
    not os.getenv("PROD_DUMP"),
    reason="needs a fresh dump from the VPS: PROD_DUMP=/path/to/bot_data.db. "
           "The local bot_data.db is a March artefact — two users, 22 orders, no "
           "broadcast, stock or events tables — so migrating it proves nothing "
           "about the path production will take.",
)
def test_schema_migrates_a_production_dump(tmp_path, monkeypatch):
    import shutil

    dump = tmp_path / "prod.db"
    shutil.copy(os.environ["PROD_DUMP"], dump)

    before = sqlite3.connect(dump)
    tables_before = {r[0] for r in before.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    version_before = before.execute("PRAGMA user_version").fetchone()[0]
    counts_before = {t: before.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                     for t in tables_before if not t.startswith("sqlite_")}
    before.close()

    monkeypatch.setattr(botdb, "DB_PATH", str(dump))
    asyncio.run(botdb.init_db())

    after = sqlite3.connect(dump)
    assert after.execute("PRAGMA user_version").fetchone()[0] == botdb.SCHEMA_VERSION
    assert after.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    for table, count in counts_before.items():
        assert after.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == count, (
            f"{table} lost rows during the migration (was {count})"
        )
    after.close()
    print(f"\nmigrated from user_version={version_before}, tables={sorted(tables_before)}")


def test_fresh_database_has_every_column_the_code_uses(tmp_path, monkeypatch):
    """A fresh database is stamped, not migrated, so the late columns have to be
    applied on that path too.

    They were not. `users` came out holding chat_id, phone and created_at alone,
    and every language lookup, profile write and language change failed with
    "no such column" from the first /start. Production predates those columns
    and got them through migration 1, so only a genuinely new deployment ever
    saw it — which is the kind of bug that waits for the day the server is
    rebuilt.
    """
    path = tmp_path / "fresh.db"
    monkeypatch.setattr(botdb, "DB_PATH", str(path))
    asyncio.run(botdb.init_db())

    db = sqlite3.connect(path)
    users = {r[1] for r in db.execute("PRAGMA table_info(users)")}
    orders = {r[1] for r in db.execute("PRAGMA table_info(orders)")}
    db.close()

    for table, column, _decl in botdb._LATE_COLUMNS:
        present = users if table == "users" else orders
        assert column in present, f"{table}.{column} missing from a fresh database"
