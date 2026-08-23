"""The three tables revision 001 left for later, which have since arrived.

Revision ID: 002
Revises: 001
Create Date: 2026-08-23

001 said it would carry "only the twelve tables that have code behind them",
because "a table with nothing reading or writing it is documentation
impersonating a schema". That was true on 2026-08-03. It is not true now: the
outbox has 341 lines of repository behind it and rows in production, the
incremental sync keeps its cursor in `sync_state`, and `user_crm_buyers`
records which CRM buyers a chat has been seen as.

Found by preparing the data transfer (point 4 of docs/postgres-migration.md):
the SQLite database has fifteen tables and Postgres had twelve, so three had
nowhere to land. Losing `sync_state.cursor` in particular would not be a lost
table — it would be an incremental sync that silently starts over.

**Same rules as 001, applied mechanically rather than reinvented:** every
timestamp becomes `timestamptz`; anything owned by a person references
`users.id` rather than carrying a chat id; the surrogate primary key is a
BigInteger. `respect_quiet` becomes a real boolean, since SQLite only had an
INTEGER to store one in.

**No role or grant work here.** 001's `_create_application_role()` hardcodes
schema `public` and therefore granted nothing once the tables landed in `tgbot`;
its docstring also promises a FORCE that the code never applies. Both are real
and both are left alone deliberately — who the application connects as, and
whether it is the owner of its own tables, decides whether RLS can ever apply,
and that is a decision for the switch (point 5), not a side effect of adding
three tables.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "002"
down_revision = "001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "outbox",
        sa.Column("id", sa.BigInteger, primary_key=True),
        # Was chat_id. §4.6's rule: user-owned rows reference the person, not
        # the messenger account they happened to arrive through.
        sa.Column("user_id", sa.BigInteger,
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("type", sa.Text, nullable=False),
        sa.Column("campaign_key", sa.Text, nullable=False),
        sa.Column("payload", sa.Text, nullable=False, server_default="{}"),
        sa.Column("dedup_key", sa.Text),
        sa.Column("on_uncertain", sa.Text, nullable=False, server_default="retry"),
        sa.Column("not_before", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.text("now()")),
        sa.Column("locked_until", sa.DateTime(timezone=True)),
        sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
        sa.Column("sent_at", sa.DateTime(timezone=True)),
        sa.Column("failed_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.text("now()")),
        # SQLite had an INTEGER because it has no boolean. Here it is one.
        sa.Column("respect_quiet", sa.Boolean, nullable=False,
                  server_default=sa.text("true")),
    )
    # Both partial, both carried over verbatim in intent: the sender only ever
    # scans what has not been sent, and dedup_key is unique only where present —
    # a global unique index would let one NULL block every other.
    op.execute(
        "CREATE INDEX ix_outbox_ready ON outbox (not_before) WHERE sent_at IS NULL"
    )
    op.execute(
        "CREATE UNIQUE INDEX ux_outbox_dedup ON outbox (dedup_key) "
        "WHERE dedup_key IS NOT NULL"
    )

    op.create_table(
        "sync_state",
        # One row per source, keyed by its name. No surrogate: the name is the
        # identity here and cannot become somebody else's.
        sa.Column("source", sa.Text, primary_key=True),
        sa.Column("cursor", sa.Text),
        sa.Column("last_run_at", sa.DateTime(timezone=True)),
        sa.Column("last_success_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.Text),
        sa.Column("last_full_at", sa.DateTime(timezone=True)),
    )

    op.create_table(
        "user_crm_buyers",
        sa.Column("user_id", sa.BigInteger,
                  sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        # Text, not a number: it is the CRM's identifier and only the CRM gets
        # to say what shape it has.
        sa.Column("buyer_id", sa.Text, primary_key=True),
        sa.Column("seen_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.text("now()")),
    )

    # Present in SQLite since the CRM-recheck sweep, absent from 001 because it
    # arrived after it was written. Nullable: NULL means never checked, which is
    # what two of the three production rows say today.
    op.add_column("users", sa.Column("crm_checked_at", sa.DateTime(timezone=True)))


def downgrade() -> None:
    op.drop_column("users", "crm_checked_at")
    for table in ("user_crm_buyers", "sync_state", "outbox"):
        op.drop_table(table)
