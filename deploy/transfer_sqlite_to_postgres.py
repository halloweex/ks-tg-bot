"""Move the shop's SQLite database into Postgres. Point 4 of docs/postgres-migration.md.

Run against a **fresh dump from the VPS**, never the local `bot_data.db` — that
one is a March artefact and anything passes on it. The nightly backup at 03:30
is the right source: copying the live file while the bot writes to it produces a
torn snapshot, which is the same lesson the analytics side learned about DuckDB.

    python deploy/transfer_sqlite_to_postgres.py /path/to/bot_data.db
      DATABASE_URL=postgresql://…      where to write
      --truncate                       clear the target first (rehearsals)
      --dry-run                        map and count, write nothing

**Rule-driven rather than fifteen hand-written INSERTs.** The mapping is derived
by comparing the two schemas and applying four rules, and a column that cannot
be placed is a loud error rather than a silent omission. Hand-written statements
would have been fifteen chances to transpose two columns of the same type, which
is the class of mistake that survives every test and shows up as somebody's
phone number in the name field.

Everything happens in ONE transaction. A failure halfway leaves the target
exactly as it was, so a rehearsal can be repeated without cleaning up first.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sqlite3
import sys
from datetime import datetime, timezone
from typing import Any

import asyncpg

# Load order matters: `users` first, because everything user-owned is resolved
# through the chat_id → users.id map it produces, and Postgres enforces the
# foreign keys SQLite only implied.
TABLES = (
    "users",
    "orders",
    "opt_out",
    "outbox",
    "user_crm_buyers",
    "stock_subscriptions",
    "support_threads",
    "support_albums",
    "discount_requests",
    "broadcast_jobs",
    "broadcast_targets",
    "events",
    "fsm_state",
    "stock_levels",
    "sync_state",
)

# Renames. Everything else is matched by name; anything left over stops the run.
RENAMES: dict[str, dict[str, str]] = {
    "users": {"chat_id": "tg_chat_id", "phone": "phone_normalized"},
}

# `chat_id` becomes `user_id` everywhere except `users`, where it is the
# messenger account itself and keeps its own column.
CHAT_TO_USER = tuple(t for t in TABLES if t not in ("users", "sync_state", "stock_levels"))

# Columns that exist in SQLite and deliberately do not travel. Empty on purpose:
# every column found so far has a destination, and the day one does not, saying
# so here is a decision someone made rather than a line that quietly went
# missing.
DROPPED: dict[str, set[str]] = {}


def _to_utc(value: Any) -> datetime | None:
    """SQLite text to an aware datetime.

    Two shapes appear in this database and both are UTC, for different reasons:
    `2026-07-30 18:09:48` is written by SQLite's own `datetime('now')`, which is
    UTC by definition; `2023-12-03T14:22:00.000000Z` comes from the CRM and says
    so with the Z. Naive input is therefore read as UTC rather than guessed at —
    the same rule `core/repos/pg.py` applies to live writes, and the two must
    agree or history and new rows drift apart by hours.
    """
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


async def _pg_columns(conn: asyncpg.Connection, table: str) -> dict[str, str]:
    rows = await conn.fetch(
        "SELECT column_name, data_type FROM information_schema.columns "
        "WHERE table_schema = current_schema() AND table_name = $1", table)
    return {r["column_name"]: r["data_type"] for r in rows}


def _sqlite_columns(db: sqlite3.Connection, table: str) -> list[str]:
    return [r[1] for r in db.execute(f"PRAGMA table_info({table})")]


def _coerce(value: Any, pg_type: str) -> Any:
    if value is None:
        return None
    if pg_type.startswith("timestamp"):
        return _to_utc(value)
    if pg_type == "boolean":
        # SQLite stores these as 0/1 integers; asyncpg will not take an int.
        return bool(value)
    if pg_type in ("bigint", "integer", "smallint"):
        return int(value)
    if pg_type in ("double precision", "real", "numeric"):
        return float(value)
    return value


async def transfer(sqlite_path: str, dsn: str, *, truncate: bool, dry_run: bool) -> int:
    db = sqlite3.connect(sqlite_path)
    db.row_factory = sqlite3.Row
    conn = await asyncpg.connect(dsn)
    problems: list[str] = []
    moved: dict[str, int] = {}

    tx = conn.transaction()
    await tx.start()
    done = False
    try:
        if truncate and not dry_run:
            # One statement so the FKs do not have to be dropped and restored.
            await conn.execute(
                "TRUNCATE " + ", ".join(TABLES) + " RESTART IDENTITY CASCADE")

        chat_to_user: dict[int, int] = {}

        for table in TABLES:
            src_cols = _sqlite_columns(db, table)
            dst_cols = await _pg_columns(conn, table)
            if not dst_cols:
                problems.append(f"{table}: no such table in Postgres")
                continue

            renames = RENAMES.get(table, {})
            dropped = DROPPED.get(table, set())
            plan: list[tuple[str, str]] = []          # (sqlite column, pg column)
            for c in src_cols:
                if c in dropped:
                    continue
                target = renames.get(c)
                if target is None:
                    target = "user_id" if (c == "chat_id" and table in CHAT_TO_USER) else c
                if target not in dst_cols:
                    problems.append(
                        f"{table}.{c} has nowhere to go "
                        f"(looked for {table}.{target})")
                    continue
                plan.append((c, target))

            rows = list(db.execute(f"SELECT * FROM {table}"))
            values = []
            for row in rows:
                out = []
                for src, dst in plan:
                    v = row[src]
                    if dst == "user_id" and table in CHAT_TO_USER:
                        # The map is total or the run stops: an order whose owner
                        # is missing would otherwise be written with a NULL owner
                        # and look like the not-yet-linked rows of a later stage.
                        if v not in chat_to_user:
                            problems.append(
                                f"{table}: chat_id {v} has no user — "
                                f"the map is built from `users` and must cover it")
                            out.append(None)
                            continue
                        v = chat_to_user[v]
                    out.append(_coerce(v, dst_cols[dst]))
                values.append(tuple(out))

            if dry_run:
                if table == "users":
                    # A provisional map, so the rest of the dry run checks what
                    # it is meant to check. Without it every user-owned row
                    # reports a missing owner and the report is about the dry
                    # run rather than about the data — which is exactly how this
                    # first came out.
                    chat_idx = [dst for _, dst in plan].index("tg_chat_id")
                    for n, v in enumerate(values, start=1):
                        chat_to_user[v[chat_idx]] = n
                moved[table] = len(values)
                continue

            if table == "users":
                # The only table whose ids are minted here rather than carried:
                # RETURNING gives back what the sequence chose, and the map that
                # every other table is resolved through is built from it.
                cols = ", ".join(dst for _, dst in plan)
                ph = ", ".join(f"${i}" for i in range(1, len(plan) + 1))
                chat_idx = [dst for _, dst in plan].index("tg_chat_id")
                for v in values:
                    new_id = await conn.fetchval(
                        f"INSERT INTO users ({cols}) VALUES ({ph}) RETURNING id", *v)
                    chat_to_user[v[chat_idx]] = new_id
            elif values:
                cols = ", ".join(dst for _, dst in plan)
                ph = ", ".join(f"${i}" for i in range(1, len(plan) + 1))
                await conn.executemany(
                    f"INSERT INTO {table} ({cols}) VALUES ({ph})", values)
            moved[table] = len(values)

        if problems:
            # Deduplicated and capped: one missing user produces a line per row
            # that references it, and ninety identical sentences hide the second
            # distinct problem rather than emphasising the first.
            seen: list[str] = []
            for pr in problems:
                if pr not in seen:
                    seen.append(pr)
            shown = seen[:15]
            more = f"\n  … and {len(seen) - 15} more distinct" if len(seen) > 15 else ""
            raise RuntimeError(
                f"mapping is incomplete ({len(seen)} distinct, "
                f"{len(problems)} occurrences):\n  " + "\n  ".join(shown) + more)

        if not dry_run:
            # THE STEP THAT IS EASY TO FORGET AND FAILS IN PRODUCTION.
            #
            # Rows carry their SQLite ids, so nothing ever drew from the
            # sequences and they are still at 1 — `TRUNCATE ... RESTART
            # IDENTITY` guarantees it. The first INSERT after the switch would
            # then ask for id 1, which is taken, and the bot's first event or
            # order would die on a duplicate key. Found by trying to write as
            # the application role rather than by reading the script.
            seqs = await conn.fetch(
                "SELECT c.relname AS tbl, a.attname AS col, "
                "       pg_get_serial_sequence(quote_ident(n.nspname)||'.'||"
                "                              quote_ident(c.relname), a.attname) AS seq "
                "FROM pg_class c "
                "JOIN pg_namespace n ON n.oid = c.relnamespace "
                "JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum > 0 "
                "WHERE n.nspname = current_schema() AND c.relkind = 'r' "
                "  AND pg_get_serial_sequence(quote_ident(n.nspname)||'.'||"
                "                             quote_ident(c.relname), a.attname) IS NOT NULL")
            for s in seqs:
                await conn.execute(
                    f"SELECT setval($1, COALESCE((SELECT MAX({s['col']}) "
                    f"FROM {s['tbl']}), 0) + 1, false)", s["seq"])
            print(f"  sequences advanced: {len(seqs)}")

        if dry_run:
            await tx.rollback()
            done = True
        else:
            # Counted from the target, not from what we think we sent.
            print("\n  table                      sqlite  postgres")
            bad = 0
            for table in TABLES:
                got = await conn.fetchval(f"SELECT count(*) FROM {table}")
                mark = "" if got == moved[table] else "   ← MISMATCH"
                bad += got != moved[table]
                print(f"  {table:<26} {moved[table]:>6}  {got:>8}{mark}")
            if bad:
                raise RuntimeError(f"{bad} table(s) disagree; nothing committed")
            await tx.commit()
            done = True
    except Exception:
        # Nothing lands unless the whole thing landed. A half-transferred
        # database is worse than none: it looks like a database.
        if not done:
            await tx.rollback()
        raise
    finally:
        await conn.close()
        db.close()

    if dry_run:
        print("\n  dry run — mapping complete, nothing written")
        for table in TABLES:
            print(f"  {table:<26} {moved[table]:>6}")
    return sum(moved.values())


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("sqlite_path")
    ap.add_argument("--truncate", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    dsn = os.getenv("DATABASE_URL", "")
    if not dsn:
        print("DATABASE_URL is not set", file=sys.stderr)
        return 2
    if not os.path.exists(args.sqlite_path):
        print(f"no such file: {args.sqlite_path}", file=sys.stderr)
        return 2

    total = asyncio.run(transfer(args.sqlite_path, dsn,
                                 truncate=args.truncate, dry_run=args.dry_run))
    print(f"\n  {total} rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
