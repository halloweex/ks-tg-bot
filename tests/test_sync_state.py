"""The row that decides what the next sweep reads, and whether anyone is told.

Every test here is about one of two failures. A cursor that moves when the
window was not fully read loses the orders inside it permanently, because
nothing ever asks for that range again. And a success that quietly stops being
recorded is the failure §5.5 is written about: no exception, no error column, no
alert — just data that stopped moving.
"""
from __future__ import annotations

import asyncio

import pytest

from core.repos import base as repos_base
from core.repos import sync_state
from core.repos.schema import init_db

SOURCE = "keycrm"
WINDOW_END = "2026-08-04 16:20:00"


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(repos_base, "DB_PATH", str(tmp_path / "bot_data.db"))
    asyncio.run(init_db())
    return sync_state


def test_a_source_nobody_swept_yet_has_no_row(db):
    """None and not a zero cursor: "never read" is what makes the first sweep
    take the reconciliation window instead of the last two minutes."""
    assert asyncio.run(db.get_state(SOURCE)) is None


def test_a_started_run_is_visible_before_it_finishes(db):
    asyncio.run(db.begin_run(SOURCE))
    state = asyncio.run(db.get_state(SOURCE))
    assert state["last_run_at"] is not None
    assert state["last_success_at"] is None
    assert state["cursor"] is None


def test_success_moves_the_cursor(db):
    asyncio.run(db.begin_run(SOURCE))
    asyncio.run(db.finish_success(SOURCE, WINDOW_END))
    state = asyncio.run(db.get_state(SOURCE))
    assert state["cursor"] == WINDOW_END
    assert state["last_success_at"] is not None
    assert state["last_error"] is None


def test_failure_leaves_the_cursor_where_it_was(db):
    """The whole point of the table. A window read halfway must be read again."""
    asyncio.run(db.finish_success(SOURCE, WINDOW_END))
    asyncio.run(db.begin_run(SOURCE))
    asyncio.run(db.finish_failure(SOURCE, "ReadTimeout: page 3"))

    state = asyncio.run(db.get_state(SOURCE))
    assert state["cursor"] == WINDOW_END
    assert state["last_error"] == "ReadTimeout: page 3"


def test_failure_leaves_the_last_success_alone(db):
    """The alert measures the age of the last success, so a failure must not
    look like one — and must not erase the one before it either."""
    asyncio.run(db.finish_success(SOURCE, WINDOW_END))
    succeeded_at = asyncio.run(db.get_state(SOURCE))["last_success_at"]

    asyncio.run(db.finish_failure(SOURCE, "boom"))
    assert asyncio.run(db.get_state(SOURCE))["last_success_at"] == succeeded_at


def test_a_success_clears_the_previous_error(db):
    asyncio.run(db.finish_failure(SOURCE, "boom"))
    asyncio.run(db.finish_success(SOURCE, WINDOW_END))
    assert asyncio.run(db.get_state(SOURCE))["last_error"] is None


def test_only_a_full_sweep_stamps_the_reconciliation(db):
    asyncio.run(db.finish_success(SOURCE, WINDOW_END))
    assert asyncio.run(db.get_state(SOURCE))["last_full_at"] is None

    asyncio.run(db.finish_success(SOURCE, WINDOW_END, full=True))
    assert asyncio.run(db.get_state(SOURCE))["last_full_at"] is not None


def test_an_incremental_sweep_does_not_postpone_the_next_reconciliation(db):
    """COALESCE, not excluded. Overwriting last_full_at on every two-minute run
    would push the weekly pass one interval into the future, forever — and the
    reconciliation is the only thing that catches an order the cursor skipped."""
    asyncio.run(db.finish_success(SOURCE, WINDOW_END, full=True))
    stamped = asyncio.run(db.get_state(SOURCE))["last_full_at"]

    asyncio.run(db.finish_success(SOURCE, "2026-08-04 16:22:00"))
    assert asyncio.run(db.get_state(SOURCE))["last_full_at"] == stamped


# --- through the port -------------------------------------------------------

def test_a_cursor_too_damaged_to_read_comes_back_as_missing(db):
    """The deciding moved here in commit 21, and the direction of it is the
    whole point. `plan_window` used to parse the column itself and treat an
    unreadable value as "no cursor", which is the widest window. Now the
    adapter decides, once — and if it ever decided the other way, by raising or
    by inventing a recent moment, the sweep would skip the orders it could not
    place a boundary around, permanently.
    """
    from core.repos.sync_state import SqliteSyncJournal

    asyncio.run(db.finish_success(SOURCE, WINDOW_END))

    async def damage_it() -> None:
        from core.repos.base import connect

        async with connect() as conn:
            await conn.execute("UPDATE sync_state SET cursor = 'yesterday-ish'")
            await conn.commit()

    asyncio.run(damage_it())
    state = asyncio.run(SqliteSyncJournal().state(SOURCE))
    assert state is not None, "the row is still there; only the value is unreadable"
    assert state.cursor is None


def test_a_moment_survives_the_round_trip_through_the_port(db):
    """Written as a datetime and read back as one, to the second. The column is
    text in between, and that spelling is the adapter's business alone."""
    from datetime import datetime, timezone

    from core.repos.sync_state import SqliteSyncJournal

    moment = datetime(2026, 8, 4, 16, 20, 0, tzinfo=timezone.utc)
    journal = SqliteSyncJournal()
    asyncio.run(journal.finished(SOURCE, moment))

    state = asyncio.run(journal.state(SOURCE))
    assert state.cursor == moment
    assert asyncio.run(db.get_state(SOURCE))["cursor"] == WINDOW_END


def test_a_long_error_is_truncated(db):
    """httpx puts the whole request URL in the message; this column is read by a
    person, and an alert quoting it should stay an alert."""
    asyncio.run(db.finish_failure(SOURCE, "x" * 5000))
    assert len(asyncio.run(db.get_state(SOURCE))["last_error"]) == 500


def test_the_migration_creates_the_table_on_its_own(tmp_path):
    """Called directly, because init_db() creates every table before migrating.

    That order is what makes the assertion in the obvious version of this test
    ("the table exists afterwards") true whether or not migration 7 is wired up
    at all — the same is true of migrations 4 to 6, which follow this pattern.
    What has to hold is that a database at version 6 gets the table from the
    migration, since that is the path production takes.
    """
    import aiosqlite

    from core.repos.schema import _migration_7_sync_state

    async def migrate_a_bare_database() -> list[str]:
        async with aiosqlite.connect(tmp_path / "bare.db") as db:
            await _migration_7_sync_state(db)
            cursor = await db.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'")
            return [row[0] for row in await cursor.fetchall()]

    assert "sync_state" in asyncio.run(migrate_a_bare_database())


def test_an_existing_database_ends_up_at_the_current_version(tmp_path, monkeypatch):
    """A database from before this table existed comes out usable and stamped."""
    import aiosqlite

    from core.repos.schema import SCHEMA_VERSION

    path = tmp_path / "old.db"
    monkeypatch.setattr(repos_base, "DB_PATH", str(path))

    async def make_old_database() -> None:
        async with aiosqlite.connect(path) as old:
            await old.execute(
                "CREATE TABLE users (chat_id INTEGER PRIMARY KEY, phone TEXT NOT NULL, "
                "created_at TEXT NOT NULL DEFAULT (datetime('now')))"
            )
            await old.execute("PRAGMA user_version = 6")
            await old.commit()

    async def version() -> int:
        async with aiosqlite.connect(path) as db:
            cursor = await db.execute("PRAGMA user_version")
            return (await cursor.fetchone())[0]

    asyncio.run(make_old_database())
    asyncio.run(init_db())

    assert asyncio.run(version()) == SCHEMA_VERSION
    asyncio.run(sync_state.finish_success(SOURCE, WINDOW_END))
    assert asyncio.run(sync_state.get_state(SOURCE))["cursor"] == WINDOW_END


# --- the migration this file learned about the hard way ----------------------

def test_a_new_late_column_forces_a_version_bump():
    """A tripwire, and it exists because the thing it guards took production down.

    `_LATE_COLUMNS` is applied on a fresh database and from inside a numbered
    migration. It is never applied to a database already stamped at
    SCHEMA_VERSION, because `_migrate` returns before the loop — which is every
    real installation. So adding a row there and nothing else gives the column
    to new deployments and to nobody else, and on 2026-08-26 the order sync died
    on "no such column: crm_shared_number" every two minutes for seventy-five
    minutes until the watchdog's alert was read.

    The obvious test does not catch it: stamping a database one version back
    makes the previous migration run, and that migration calls
    `_add_late_columns` too, so the column comes back whether or not the new one
    exists. What has to be caught is the *edit* — a column added without a
    version to carry it — and nothing about a database can see that.

    Hence a pin on the pair rather than a proof. Change `_LATE_COLUMNS` and this
    fails; the only way to make it pass again is to look at SCHEMA_VERSION,
    which is exactly the look that was missed.
    """
    from core.repos import schema

    assert (len(schema._LATE_COLUMNS), schema.SCHEMA_VERSION) == (16, 20), (
        "adding a column to _LATE_COLUMNS is half the change: it also needs "
        "SCHEMA_VERSION bumped and a migration that calls _add_late_columns, or "
        "it only ever reaches databases that do not exist yet"
    )


def test_the_newest_migration_is_the_current_version():
    """Cheap, and it makes the tripwire above actionable: whoever bumps
    SCHEMA_VERSION is told immediately if they forgot the migration itself."""
    from core.repos import schema

    assert schema._MIGRATIONS[-1][0] == schema.SCHEMA_VERSION


def test_a_database_from_before_the_column_gains_it(tmp_path, monkeypatch):
    """The other half: the migration, once written, really does carry it.

    A database stamped one version back must come out with every column
    `_LATE_COLUMNS` declares — which is the path any installation that upgrades
    takes.
    """
    import aiosqlite

    from core.repos import base as repos_base
    from core.repos import schema

    monkeypatch.setattr(repos_base, "DB_PATH", str(tmp_path / "bot_data.db"))

    async def scenario() -> list[str]:
        await schema.init_db()
        removed = []
        async with aiosqlite.connect(repos_base.DB_PATH) as db:
            await db.execute(f"PRAGMA user_version = {schema.SCHEMA_VERSION - 1}")
            for table, column, _decl in schema._LATE_COLUMNS:
                if column not in await schema._columns(db, table):
                    continue
                try:
                    await db.execute(f"ALTER TABLE {table} DROP COLUMN {column}")
                except Exception:
                    # SQLite refuses to drop a column an index refers to.
                    continue
                removed.append((table, column))
            await db.commit()

        await schema.init_db()
        async with aiosqlite.connect(repos_base.DB_PATH) as db:
            return [f"{t}.{c}" for t, c in removed
                    if c not in await schema._columns(db, t)]

    assert asyncio.run(scenario()) == []
