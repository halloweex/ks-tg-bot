"""The table that makes a replayed webhook worthless and a retry never lost.

A claim is atomic and leased; `busy` is not `done`; a released or expired claim
can be taken again; handled messages are remembered for the retention and then
forgotten; everything is per source; and the table reaches a live database by
migration.
"""
from __future__ import annotations

import asyncio
import sqlite3

import pytest

from core.repos import base as repos_base
from core.repos.base import connect
from core.repos.deliveries import LEASE_SECONDS, RETENTION_DAYS, SqliteDeliveryLedger
from core.repos.schema import SCHEMA_VERSION, init_db


@pytest.fixture()
def ledger(tmp_path, monkeypatch):
    monkeypatch.setattr(repos_base, "DB_PATH", str(tmp_path / "bot_data.db"))
    asyncio.run(init_db())
    return SqliteDeliveryLedger()


def run(coro):
    return asyncio.run(coro)


def _age(column: str, digest: str, seconds: int) -> None:
    async def go():
        async with connect() as db:
            await db.execute(
                f"UPDATE webhook_deliveries SET {column} = datetime('now', ?) "
                "WHERE signed_digest = ?", (f"-{seconds} seconds", digest))
            await db.commit()
    run(go())


def test_claimed_then_busy_then_done(ledger):
    assert run(ledger.claim("rivo", "d1", "msg_1")) == "claimed"
    assert run(ledger.claim("rivo", "d1", "msg_1")) == "busy"
    run(ledger.complete("rivo", "d1"))
    assert run(ledger.claim("rivo", "d1", "msg_1")) == "done"


def test_concurrent_claims_have_exactly_one_winner(ledger):
    async def race():
        return await asyncio.gather(*(ledger.claim("rivo", "d_race", "m") for _ in range(8)))
    assert sorted(run(race())) == ["busy"] * 7 + ["claimed"]


def test_a_released_claim_can_be_taken_again(ledger):
    run(ledger.claim("rivo", "d2", "m"))
    run(ledger.release("rivo", "d2"))
    assert run(ledger.claim("rivo", "d2", "m")) == "claimed"


def test_release_never_undoes_a_completed_message(ledger):
    run(ledger.claim("rivo", "d3", "m"))
    run(ledger.complete("rivo", "d3"))
    run(ledger.release("rivo", "d3"))
    assert run(ledger.claim("rivo", "d3", "m")) == "done"


def test_an_abandoned_claim_is_taken_over_after_the_lease(ledger):
    """The attempt was killed — a deploy, out of memory — and never released."""
    run(ledger.claim("rivo", "d4", "m"))
    assert run(ledger.claim("rivo", "d4", "m")) == "busy"
    _age("received_at", "d4", LEASE_SECONDS + 5)
    assert run(ledger.claim("rivo", "d4", "m")) == "claimed"


def test_a_completed_message_is_not_taken_over_when_old(ledger):
    run(ledger.claim("rivo", "d5", "m"))
    run(ledger.complete("rivo", "d5"))
    _age("received_at", "d5", LEASE_SECONDS + 5)
    assert run(ledger.claim("rivo", "d5", "m")) == "done"


def test_a_claim_inside_the_lease_is_still_busy(ledger):
    """The boundary from the other side: a lease that expired in a second would
    let a slow but healthy attempt be doubled."""
    # A fixed age a healthy handling can plausibly reach, not one derived from
    # the constant — a lease shortened to seconds must fail on the assertion.
    assert LEASE_SECONDS >= 60, "a lease shorter than a slow handling doubles it"
    run(ledger.claim("rivo", "d_young", "m"))
    _age("received_at", "d_young", 55)
    assert run(ledger.claim("rivo", "d_young", "m")) == "busy"


def test_a_takeover_renews_the_lease(ledger):
    """Taking over an abandoned claim starts a new lease; without that, every
    copy arriving after the first takeover would take it over again."""
    run(ledger.claim("rivo", "d_take", "m"))
    _age("received_at", "d_take", LEASE_SECONDS + 5)
    assert run(ledger.claim("rivo", "d_take", "m")) == "claimed"
    assert run(ledger.claim("rivo", "d_take", "m")) == "busy"


def test_a_handled_message_is_remembered_until_the_retention_ends(ledger):
    run(ledger.claim("rivo", "d_kept", "m"))
    run(ledger.complete("rivo", "d_kept"))
    _age("received_at", "d_kept", (RETENTION_DAYS - 1) * 86400)
    run(ledger.claim("rivo", "d_other2", "m"))  # prunes
    assert run(ledger.claim("rivo", "d_kept", "m")) == "done"


def test_messages_are_per_source(ledger):
    assert run(ledger.claim("rivo", "d6", "m")) == "claimed"
    assert run(ledger.claim("elsewhere", "d6", "m")) == "claimed"
    run(ledger.release("elsewhere", "d6"))
    assert run(ledger.claim("rivo", "d6", "m")) == "busy", "release crossed sources"


def test_messages_older_than_the_retention_are_forgotten(ledger):
    run(ledger.claim("rivo", "d_old", "m"))
    run(ledger.complete("rivo", "d_old"))
    _age("received_at", "d_old", (RETENTION_DAYS + 1) * 86400)
    run(ledger.claim("rivo", "d_other", "m"))  # any claim prunes
    assert run(ledger.claim("rivo", "d_old", "m")) == "claimed"


def test_the_table_arrives_on_a_live_database_by_migration(tmp_path, monkeypatch):
    """Production is at version 20. Checked on a bare connection after the
    migration alone, not through init_db's unconditional CREATE, so an empty
    migration 21 fails here."""
    import aiosqlite
    from core.repos import schema

    path = tmp_path / "live.db"
    monkeypatch.setattr(repos_base, "DB_PATH", str(path))
    run(init_db())
    con = sqlite3.connect(path)
    con.execute("DROP TABLE webhook_deliveries")
    con.commit(); con.close()

    async def go():
        async with aiosqlite.connect(path) as db:
            await schema._migration_21_webhook_deliveries(db)
            await db.commit()
    run(go())

    con = sqlite3.connect(path)
    cols = [r[1] for r in con.execute("PRAGMA table_info(webhook_deliveries)")]
    con.close()
    assert cols == ["source", "signed_digest", "delivery_id", "received_at", "done_at"]
    assert SCHEMA_VERSION == 21


def test_a_database_ahead_of_the_code_starts_and_says_so(tmp_path, monkeypatch):
    """A rollback: a newer image migrated the file and an older one starts on
    it. It must start, and it must say it, because the next migration after a
    revert needs the next free number."""
    from loguru import logger

    path = tmp_path / "ahead.db"
    monkeypatch.setattr(repos_base, "DB_PATH", str(path))
    run(init_db())
    con = sqlite3.connect(path)
    con.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 3}")
    con.commit(); con.close()

    said: list[str] = []
    sink = logger.add(lambda m: said.append(str(m)), level="WARNING")
    try:
        run(init_db())
    finally:
        logger.remove(sink)
    assert any("ahead of this code" in line for line in said), said
