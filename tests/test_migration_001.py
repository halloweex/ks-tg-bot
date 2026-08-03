"""Revision 001 compiles, and says what it is supposed to say.

Rendered offline — `alembic upgrade head --sql` needs no database — so this runs
in the ordinary suite and catches a revision that stopped compiling long before
anyone points it at Postgres.

The assertions are about the decisions, not the DDL: which column is unique,
which index is partial, and that no timestamp arrived without a timezone. Each
one is a line in docs/architecture.md that would be expensive to discover was
not implemented after the data moved.
"""
from __future__ import annotations

import subprocess

import pytest

from tests.conftest import REPO_ROOT


def _render(*args: str) -> str:
    result = subprocess.run(
        [".venv/bin/alembic", *args, "--sql"],
        cwd=REPO_ROOT, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


@pytest.fixture(scope="module")
def ddl() -> str:
    return _render("upgrade", "head")


def test_every_table_the_code_uses_is_created(ddl):
    """Twelve, and no more: a table with nothing writing to it is documentation
    impersonating a schema."""
    created = {line.split()[2] for line in ddl.splitlines()
               if line.startswith("CREATE TABLE ")}
    assert created == {
        "alembic_version", "users", "opt_out", "orders", "broadcast_jobs",
        "broadcast_targets", "events", "stock_levels", "stock_subscriptions",
        "support_threads", "support_albums", "discount_requests", "fsm_state",
    }


def test_no_timestamp_arrives_without_a_timezone(ddl):
    """§4.6: customers in Ukraine, the owner in Dubai, suppliers in Korea. The
    migration is the one cheap moment to fix this, and a single naive column
    would be found months later by a delivery date an hour out."""
    for line in ddl.splitlines():
        if "TIMESTAMP" in line:
            assert "TIMESTAMP WITH TIME ZONE" in line, line


def test_the_phone_is_unique_for_users_and_not_for_orders(ddl):
    """Measured, not assumed: 203 numbers in the CRM belong to more than one
    buyer (§2C), so a unique constraint on orders.phone_normalized would fail
    partway through the data migration."""
    users, orders = ddl.split("CREATE TABLE orders")[0], ddl.split("CREATE TABLE orders")[1]
    assert "UNIQUE (phone_normalized)" in users
    assert "UNIQUE (phone_normalized)" not in orders.split("CREATE TABLE")[0]


def test_the_merge_index_is_partial_and_scoped_to_the_user(ddl):
    """Both halves matter and both are §4.4: the predicate keeps empty keys out,
    a global key would collapse two accounts sharing a phone into one row."""
    assert ("CREATE UNIQUE INDEX ux_orders_merge ON orders (user_id, merge_key) "
            "WHERE merge_key <> ''") in ddl


def test_orders_can_exist_before_their_owner_does(ddl):
    """§5.4: a full sync writes an order before it is known whether the buyer
    has Telegram at all, so user_id is nullable and the linker fills it in."""
    orders = ddl.split("CREATE TABLE orders")[1].split(");")[0]
    assert "user_id BIGINT, " in orders, "user_id must be nullable"


def test_the_application_role_does_not_own_the_tables(ddl):
    """§4.7's first trap: the owner bypasses RLS by default, so an application
    connecting as the migrating role would see every row whatever the policies
    say. The role is created and granted, never made owner."""
    assert "CREATE ROLE ks_app LOGIN" in ddl
    assert "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES" in ddl
    assert "ALTER TABLE" not in ddl.replace("ALTER TABLE ... OWNER", ""), \
        "no ownership changes belong in this revision"


def test_later_tables_inherit_the_grants(ddl):
    """Without default privileges a table added by revision 002 is invisible to
    the application until someone remembers to grant it."""
    assert "ALTER DEFAULT PRIVILEGES IN SCHEMA public" in ddl


def test_it_rolls_back(ddl):
    """Not the production escape hatch — that is the untouched SQLite file and
    a 48-hour window (§14) — but what makes the revision re-runnable against a
    scratch database while it is being written."""
    down = _render("downgrade", "001:base")
    assert down.count("DROP TABLE") == 13
