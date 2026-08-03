"""Initial Postgres schema — the twelve tables the code has today, in target form.

Revision ID: 001
Revises:
Create Date: 2026-08-03

Written by hand against Postgres. The SQLite history does not enter this file
(docs/architecture.md §10): six SQLite migrations describe how that database got
to where it is, and replaying them onto a database that never had that shape
would be a longer road to the same place with more to go wrong.

**Only the twelve tables that have code behind them.** `outbox`, `shipments`,
`order_items` and the rest of §4.6 arrive with the stages that write to them —
a table with nothing reading or writing it is documentation impersonating a
schema, and it goes stale the same way.

**What changes shape here, and what deliberately does not.** Only what §4.6 asks
for by name:

* every timestamp becomes `timestamptz`, stored in UTC — customers in Ukraine,
  the owner in Dubai, suppliers in Korea, and the migration is the one cheap
  moment to fix it;
* `users` gets a surrogate `id` and every user-owned table references it rather
  than `chat_id` — a person can change Telegram account and keep their number;
* `orders.user_id` is nullable, because the sync writes an order before it is
  known whether the buyer has Telegram at all (§5.4).

Not changed, though both are tempting: `grand_total` stays double precision
rather than becoming `numeric`, and `products_json` stays text rather than
`jsonb`. Neither is named in §4.6, both change what Python receives, and an
engine migration that also fixes money types is two changes inside one rollback
window — where, if something goes wrong, neither can be told from the other.
They are follow-ups, listed in docs/postgres-migration.md.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "001"
down_revision = None
branch_labels = None
depends_on = None

# The role the application connects as. It does not own the tables, which is
# half of what makes RLS real — the owner bypasses policies by default, and that
# is the first of the two traps in §4.7. The other half is FORCE below.
APP_ROLE = "ks_app"


def upgrade() -> None:
    # ---- identity ---------------------------------------------------------
    op.create_table(
        "users",
        sa.Column("id", sa.BigInteger, primary_key=True),
        # The Telegram account. Unique, but not the identity: it is an artefact
        # of one messenger and the person behind it can get a new one.
        sa.Column("tg_chat_id", sa.BigInteger, nullable=False, unique=True),
        # Unique here and only here. In `orders` the same column is an
        # attribute: 203 numbers in the CRM belong to more than one buyer
        # (docs/architecture.md §2C), so uniqueness there would be false.
        sa.Column("phone_normalized", sa.Text, nullable=False, unique=True),
        sa.Column("full_name", sa.Text),
        sa.Column("email", sa.Text),
        # NULL means "never chosen": the Telegram language still decides.
        sa.Column("language", sa.Text),
        # First touch, written once at registration — the deep link they opened,
        # empty for anyone who found the bot by themselves.
        sa.Column("source", sa.Text, nullable=False, server_default=""),
        # §6.5: proactive messages only about events after this. Without it a
        # customer who registers and gets their history linked receives forty
        # notifications about parcels that arrived in 2024.
        sa.Column("notify_from", sa.DateTime(timezone=True)),
        # §4.8: the confirmed boundary of automatic order linking, so a repeat
        # run cannot widen it silently.
        sa.Column("linked_up_to", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
    )

    # Kept as it is rather than reshaped into the separate consents §4.6 asks
    # for — that is its own step, after the engine move.
    op.create_table(
        "opt_out",
        sa.Column("user_id", sa.BigInteger,
                  sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("opted_out_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.text("now()")),
    )

    # ---- orders -----------------------------------------------------------
    op.create_table(
        "orders",
        sa.Column("id", sa.BigInteger, primary_key=True),
        # Nullable on purpose: a full sync writes the order before it is known
        # whether this buyer has Telegram (§5.4). The linker fills it in later,
        # under the rules in §4.8.
        sa.Column("user_id", sa.BigInteger, sa.ForeignKey("users.id"), nullable=True),
        # Always filled, never unique — see the note on users.phone_normalized.
        sa.Column("phone_normalized", sa.Text, nullable=False, server_default=""),
        sa.Column("source", sa.Text, nullable=False),
        sa.Column("source_order_id", sa.Text, nullable=False),
        sa.Column("external_id", sa.Text, nullable=False, server_default=""),
        # Identity of the physical order across systems (core/domain/order.py).
        sa.Column("merge_key", sa.Text, nullable=False, server_default=""),
        sa.Column("source_rank", sa.Integer, nullable=False, server_default="0"),
        sa.Column("order_name", sa.Text, nullable=False, server_default=""),
        sa.Column("status_name", sa.Text, nullable=False, server_default=""),
        sa.Column("status_group_id", sa.Integer, nullable=False, server_default="0"),
        sa.Column("grand_total", sa.Float, nullable=False, server_default="0"),
        sa.Column("currency", sa.Text, nullable=False, server_default=""),
        sa.Column("ordered_at", sa.DateTime(timezone=True)),
        sa.Column("products_json", sa.Text, nullable=False, server_default="[]"),
        sa.Column("buyer_name", sa.Text, nullable=False, server_default=""),
        sa.Column("payment_status", sa.Text, nullable=False, server_default=""),
        sa.Column("tracking_code", sa.Text, nullable=False, server_default=""),
        sa.Column("shipping_status", sa.Text, nullable=False, server_default=""),
        sa.Column("delivery_city", sa.Text, nullable=False, server_default=""),
        sa.Column("receive_point", sa.Text, nullable=False, server_default=""),
        sa.Column("recipient_name", sa.Text, nullable=False, server_default=""),
        sa.Column("synced_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("user_id", "source", "source_order_id",
                            name="uq_orders_user_source_id"),
    )
    op.create_index("ix_orders_user", "orders", ["user_id"])
    op.create_index("ix_orders_phone", "orders", ["phone_normalized"])
    op.create_index("ix_orders_external", "orders", ["user_id", "external_id"])
    # Partial, and scoped to the user rather than global — both deliberate,
    # §4.4. Global would collapse two accounts sharing a phone onto one row,
    # which is the bug the key was widened to fix; it widens in the stage that
    # makes linking work, not here. The predicate keeps rows with no merge_key
    # under the plain unique constraint above.
    op.execute(
        "CREATE UNIQUE INDEX ux_orders_merge ON orders (user_id, merge_key) "
        "WHERE merge_key <> ''"
    )

    # ---- broadcast --------------------------------------------------------
    op.create_table(
        "broadcast_jobs",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("status", sa.Text, nullable=False, server_default="running"),
        sa.Column("created_by", sa.BigInteger),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.text("now()")),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
    )
    op.create_table(
        "broadcast_targets",
        sa.Column("job_id", sa.BigInteger,
                  sa.ForeignKey("broadcast_jobs.id", ondelete="CASCADE"),
                  primary_key=True),
        sa.Column("user_id", sa.BigInteger,
                  sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("status", sa.Text, nullable=False, server_default="pending"),
        sa.Column("error", sa.Text),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
    )
    # Resuming a job scans its still-pending targets, and only those.
    op.execute(
        "CREATE INDEX ix_targets_pending ON broadcast_targets (job_id) "
        "WHERE status = 'pending'"
    )

    # ---- analytics --------------------------------------------------------
    op.create_table(
        "events",
        sa.Column("id", sa.BigInteger, primary_key=True),
        # Nullable: some events happen before anyone is registered.
        sa.Column("user_id", sa.BigInteger,
                  sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("event", sa.Text, nullable=False),
        sa.Column("meta", sa.Text, nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_events_name_time", "events", ["event", "created_at"])
    op.create_index("ix_events_user_time", "events", ["user_id", "created_at"])

    # ---- stock ------------------------------------------------------------
    op.create_table(
        "stock_levels",
        sa.Column("sku", sa.Text, primary_key=True),
        sa.Column("available", sa.Integer, nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.text("now()")),
    )
    op.create_table(
        "stock_subscriptions",
        sa.Column("user_id", sa.BigInteger,
                  sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("sku", sa.Text, primary_key=True),
        sa.Column("name", sa.Text, nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.text("now()")),
    )
    # A restock fans out to everyone waiting on that sku.
    op.create_index("ix_stock_subs_sku", "stock_subscriptions", ["sku"])

    # ---- support ----------------------------------------------------------
    op.create_table(
        "support_threads",
        sa.Column("admin_message_id", sa.BigInteger, primary_key=True),
        sa.Column("user_id", sa.BigInteger,
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.text("now()")),
    )
    op.create_table(
        "support_albums",
        sa.Column("user_id", sa.BigInteger,
                  sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("media_group_id", sa.Text, primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.text("now()")),
    )
    op.create_table(
        "discount_requests",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("user_id", sa.BigInteger,
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("products_json", sa.Text, nullable=False, server_default="[]"),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.text("now()")),
    )

    # ---- conversation state ----------------------------------------------
    # Keyed by the string aiogram builds, not by user_id: the key exists before
    # anyone is registered, which is exactly when onboarding needs it.
    op.create_table(
        "fsm_state",
        sa.Column("key", sa.Text, primary_key=True),
        sa.Column("state", sa.Text),
        sa.Column("data", sa.Text, nullable=False, server_default="{}"),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.text("now()")),
    )

    _create_application_role()


def _create_application_role() -> None:
    """The unprivileged role, and FORCE on every table that will need a policy.

    Both halves of §4.7's first trap, and they land now because they are cheap
    on an empty database and awkward on one with data. The policies themselves
    are not here: they belong with the cabinet, which is the first thing that
    reads another person's row. FORCE without policies denies everything to the
    application role, so the role is granted table privileges and RLS is enabled
    only where a policy will exist — enabling it everywhere today would lock the
    bot out of its own tables the moment it connects as ks_app.
    """
    op.execute(
        f"DO $$ BEGIN "
        f"  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN "
        f"    CREATE ROLE {APP_ROLE} LOGIN; "
        f"  END IF; "
        f"END $$"
    )
    op.execute(f"GRANT USAGE ON SCHEMA public TO {APP_ROLE}")
    op.execute(
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {APP_ROLE}"
    )
    op.execute(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {APP_ROLE}")
    # Tables created by later revisions inherit the same grants, so a new table
    # does not silently become invisible to the application.
    op.execute(
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {APP_ROLE}"
    )
    op.execute(
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        f"GRANT USAGE, SELECT ON SEQUENCES TO {APP_ROLE}"
    )


def downgrade() -> None:
    """Everything, in dependency order.

    Present and correct rather than `raise NotImplementedError`: the 48-hour
    rollback window (§14) returns to the untouched SQLite file, so this is not
    the production escape hatch — it is what lets the revision be applied and
    rolled back repeatedly against a scratch database while it is being written.
    """
    for table in ("fsm_state", "discount_requests", "support_albums",
                  "support_threads", "stock_subscriptions", "stock_levels",
                  "events", "broadcast_targets", "broadcast_jobs",
                  "orders", "opt_out", "users"):
        op.drop_table(table)
    # The role is left in place: it may own grants in other databases on the
    # same cluster, and dropping a role out from under them fails anyway.
