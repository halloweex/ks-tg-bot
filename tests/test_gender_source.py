"""The reader for `app.buyer_gender`, against a real Postgres.

Written against the engine and not against a mock, for the reason
docs/postgres-migration.md gives for the unit of work: a query is not tested by
something that agrees with it. The table is created here from the same DDL the
warehouse has, so the `int[]` cast, the NULL and the absent row are the real
ones.

Skipped unless TEST_WAREHOUSE_URL is set — the production Postgres has no
published port (Rule 12 of the neighbouring project's charter). A throwaway is
enough:

    docker run -d --rm --name ks-gender-test -e POSTGRES_PASSWORD=test \\
        -e POSTGRES_DB=ks -p 55432:5432 postgres:17.2-alpine
    TEST_WAREHOUSE_URL=postgresql://postgres:test@127.0.0.1:55432/ks \\
        .venv/bin/python -m pytest tests/test_gender_source.py
"""
from __future__ import annotations

import asyncio
import os

import pytest

from core.ports.gender import BuyerGenders
from core.repos.buyer_gender import PgBuyerGenders

DSN = os.getenv("TEST_WAREHOUSE_URL", "")
warehouse_only = pytest.mark.skipif(not DSN, reason="TEST_WAREHOUSE_URL not set")

# The table as the warehouse has it. Kept in full rather than trimmed to the two
# columns this reads: a row that only satisfies the reader would not catch the
# reader asking for a column that is not there.
DDL = """
CREATE SCHEMA IF NOT EXISTS app;
DROP TABLE IF EXISTS app.buyer_gender;
CREATE TABLE app.buyer_gender (
    buyer_id          INTEGER PRIMARY KEY,
    gender            VARCHAR,
    method            VARCHAR,
    confidence        VARCHAR,
    decided_from      VARCHAR,
    rules_version     INTEGER,
    override_by_human BOOLEAN,
    decided_at        TIMESTAMPTZ
);
"""

ROWS = [
    (6477, "f", "dictionary", "certain", "given_name", 3, False),
    (9001, "m", "dictionary", "high", "given_name", 3, False),
    # The classifier having looked and refused: `method` says which layer gave
    # up, and the bot must read this as "unknown" rather than as "no row".
    (9002, None, "role_unresolved", None, None, 3, False),
    # A correction somebody made by hand. Nothing in the reader treats it
    # specially, which is the point — an override arrives as the value.
    (9003, "m", "human", "certain", None, 3, True),
]


def test_it_is_the_port():
    assert isinstance(PgBuyerGenders("postgresql://nowhere"), BuyerGenders)


def test_nothing_numeric_means_no_query_and_no_connection():
    """Asked about ids the column cannot hold, it answers nothing — without
    opening a connection, which is what makes this runnable with no database."""
    source = PgBuyerGenders("postgresql://user:pass@127.0.0.1:1/nothing")
    assert asyncio.run(source.for_buyers(set())) == {}
    assert asyncio.run(source.for_buyers({"", "abc"})) == {}


@pytest.fixture()
def warehouse():
    import asyncpg

    async def setup():
        conn = await asyncpg.connect(DSN)
        try:
            await conn.execute(DDL)
            await conn.executemany(
                "INSERT INTO app.buyer_gender (buyer_id, gender, method, "
                "confidence, decided_from, rules_version, override_by_human, "
                "decided_at) VALUES ($1,$2,$3,$4,$5,$6,$7, now())", ROWS)
        finally:
            await conn.close()

    asyncio.run(setup())
    yield PgBuyerGenders(DSN)

    async def teardown():
        conn = await asyncpg.connect(DSN)
        try:
            await conn.execute("DROP TABLE IF EXISTS app.buyer_gender")
        finally:
            await conn.close()

    asyncio.run(teardown())


@warehouse_only
def test_the_answers_come_back_keyed_the_way_the_bot_spells_a_buyer(warehouse):
    """`user_crm_buyers.buyer_id` is TEXT here and INTEGER there. The cast is the
    adapter's, and the caller must get its own spelling back or the chat's cards
    and the answer about them cannot be compared."""
    answers = asyncio.run(warehouse.for_buyers({"6477", "9001"}))
    assert answers == {"6477": "f", "9001": "m"}


@warehouse_only
def test_a_null_is_present_and_a_missing_row_is_absent(warehouse):
    """The two states the bot must not confuse: the classifier looked and
    refused, versus a buyer the table has never seen."""
    answers = asyncio.run(warehouse.for_buyers({"9002", "123456789"}))
    assert answers == {"9002": None}


@warehouse_only
def test_an_override_reads_as_the_value_it_holds(warehouse):
    assert asyncio.run(warehouse.for_buyers({"9003"})) == {"9003": "m"}


@warehouse_only
def test_one_unparseable_id_does_not_cost_the_whole_lookup(warehouse):
    """The set comes from a table this bot fills from the CRM. One bad row there
    must not stop everybody else's form from being resolved."""
    answers = asyncio.run(warehouse.for_buyers({"6477", "not-a-number", ""}))
    assert answers == {"6477": "f"}


@warehouse_only
def test_a_whole_base_is_one_round_trip(warehouse):
    """Every card in the bot's map goes in one `= ANY($1::int[])`, which is what
    keeps an hourly sweep over twenty thousand buyers a single query."""
    asked = {str(n) for n in range(1, 2000)} | {"6477", "9001", "9002", "9003"}
    answers = asyncio.run(warehouse.for_buyers(asked))
    assert answers == {"6477": "f", "9001": "m", "9002": None, "9003": "m"}
