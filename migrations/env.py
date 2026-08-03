"""Alembic entry point.

Deliberately thin and without a model registry: revisions here are written by
hand against Postgres (docs/architecture.md §10), so there is nothing to
autogenerate from and no metadata to import. That also keeps this file out of
the import graph the linter guards — it reaches for no application module.

The URL comes from DATABASE_URL. Not from core.config, even though that is the
one place allowed to read the environment: this process is Alembic's, not the
bot's, and importing the settings here would make a migration fail on a missing
BOT_TOKEN.
"""
from __future__ import annotations

import os

from alembic import context
from sqlalchemy import engine_from_config, pool

config = context.config

DATABASE_URL = os.getenv("DATABASE_URL", "")
if DATABASE_URL:
    # Escaped: a password with a '%' in it would otherwise be read as
    # configparser interpolation and truncate the URL.
    config.set_main_option("sqlalchemy.url", DATABASE_URL.replace("%", "%%"))


def run_migrations_offline() -> None:
    """Render the DDL as SQL without connecting.

    This is how a revision is reviewed before any database exists, and how CI
    can check that it compiles: `alembic upgrade head --sql`.
    """
    context.configure(
        url=config.get_main_option("sqlalchemy.url") or "postgresql://localhost/unused",
        target_metadata=None,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        dialect_name="postgresql",
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    if not config.get_main_option("sqlalchemy.url"):
        raise SystemExit("DATABASE_URL is not set — refusing to guess a database")

    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=None)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
