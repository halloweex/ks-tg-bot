-- Roles and the empty schema for the shop's own Postgres. No tables — those are
-- Alembic's, in migrations/versions/.
--
-- This file came from `key-api-bot/postgres/initdb/30-tgbot.sql`, where the shop
-- briefly lived as a schema in the shared instance. The owner decided on
-- 2026-08-23 that the shop gets its own server instead, and the reasoning that
-- was earned there is kept here rather than rediscovered:
--
--   * an OWNER BYPASSES ROW LEVEL SECURITY. Revision 001 states the requirement
--     — "It does not own the tables, which is half of what makes RLS real" —
--     and does not arrange it: its grant block hardcodes `SCHEMA public`, so
--     every statement in it is a no-op once the tables live anywhere else, and
--     its docstring promises a FORCE the code never applies. Hence two roles.
--
--   * `search_path` on the role is what sends Alembic's output to the right
--     schema. `migrations/env.py` sets no schema and no `version_table_schema`,
--     so without it twelve tables and a version ledger land in `public` —
--     quietly, and correctly by Postgres's rules.
--
--   * `/docker-entrypoint-initdb.d` runs ONLY against an empty data directory.
--     On a database that already exists a new file here is inert, so this must
--     be re-applied by something on every start. Every statement below is
--     written to be a no-op the second time.

\set app_password   `echo "$TGBOT_APP_PASSWORD"`
\set owner_password `echo "$TGBOT_OWNER_PASSWORD"`

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'tgbot_owner') THEN
        CREATE ROLE tgbot_owner LOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'tgbot_app') THEN
        CREATE ROLE tgbot_app LOGIN;
    END IF;
END
$$;

ALTER ROLE tgbot_owner WITH PASSWORD :'owner_password';
ALTER ROLE tgbot_app   WITH PASSWORD :'app_password';

CREATE SCHEMA IF NOT EXISTS tgbot AUTHORIZATION tgbot_owner;
ALTER SCHEMA tgbot OWNER TO tgbot_owner;

ALTER ROLE tgbot_owner SET search_path = tgbot, public;
ALTER ROLE tgbot_app   SET search_path = tgbot, public;

-- Moves anything that already exists, not just what is created from tomorrow.
-- Sequences included: `users.id` draws from one, and an application that cannot
-- use it cannot register anybody.
DO $$
DECLARE r record;
BEGIN
    FOR r IN SELECT tablename FROM pg_tables WHERE schemaname = 'tgbot' LOOP
        EXECUTE format('ALTER TABLE tgbot.%I OWNER TO tgbot_owner', r.tablename);
    END LOOP;
    FOR r IN SELECT sequencename FROM pg_sequences WHERE schemaname = 'tgbot' LOOP
        EXECUTE format('ALTER SEQUENCE tgbot.%I OWNER TO tgbot_owner', r.sequencename);
    END LOOP;
END
$$;

-- Everything the bot needs to run, nothing that lets it change the shape of
-- what it runs on.
GRANT USAGE ON SCHEMA tgbot TO tgbot_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA tgbot TO tgbot_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA tgbot TO tgbot_app;
ALTER DEFAULT PRIVILEGES FOR ROLE tgbot_owner IN SCHEMA tgbot
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO tgbot_app;
ALTER DEFAULT PRIVILEGES FOR ROLE tgbot_owner IN SCHEMA tgbot
    GRANT USAGE, SELECT ON SEQUENCES TO tgbot_app;

REVOKE CREATE ON SCHEMA public FROM PUBLIC;
