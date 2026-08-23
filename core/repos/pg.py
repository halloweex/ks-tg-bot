"""The unit of work over Postgres — the one where the transaction is real.

The counterpart to `core/repos/uow.py`, which says of itself that it is a shim.
Here `commit()` commits, leaving the block without it rolls back, and
`user_id` has something to be set on.

**Not wired into the application yet, on purpose.** Nothing imports this outside
its tests. `core/config.py` forbids unknown keys in `.env` (it raises
`extra_forbidden`), so introducing `DATABASE_URL` there is a change that makes
the bot refuse to start if it is half done — that belongs to the switch, point 5
of docs/postgres-migration.md, together with the IMAGE_TAG that turns it on.
Until then a DSN is passed in, which is also what lets the tests run both
implementations against the same scenario.

**Why `SET LOCAL` and not `SET`.** The setting has to die with the transaction.
A plain `SET` lives for the session, and a pooled connection outlives the
request, so the next borrower inherits the previous one's identity — the
documented multi-tenancy footgun, and the one that hands somebody else's rows to
a stranger. `SET LOCAL` cannot take a parameter in Postgres, so the form is
`set_config($1, $2, true)`, where `true` means "transaction-local".
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import TracebackType
from typing import Any

import asyncpg

from core.domain.order import merge_key, source_rank
from core.domain.phone import VerifiedPhone

# Columns written by an upsert, in order. `id` is the sequence's, `synced_at` is
# set by the statement, and `phone_normalized` is deliberately absent: the rows
# arriving here come from a sync that already knows whose they are, and the
# column exists for the later stage that writes orders before the buyer is known
# to have Telegram at all. Its server default is '' and that is the honest value
# for a row written through this path.
_ORDER_COLUMNS: tuple[str, ...] = (
    "user_id", "source", "source_order_id", "external_id",
    "merge_key", "source_rank", "order_name",
    "status_name", "status_group_id", "grand_total", "currency", "ordered_at",
    "products_json",
    "buyer_name", "payment_status", "tracking_code", "shipping_status",
    "delivery_city", "receive_point", "recipient_name",
)

# The newer read wins. Same policy as the SQLite implementation, and the comment
# there explains why the source_rank branch is gone rather than conditional.
_OVERWRITE = ("source", "source_order_id", "status_name", "status_group_id",
              "grand_total", "currency", "ordered_at", "products_json",
              "buyer_name", "payment_status", "tracking_code", "shipping_status",
              "delivery_city", "receive_point", "recipient_name")
# A non-empty incoming value fills a gap; an empty one never erases. An Instagram
# order has neither an external_id nor an order_name, and must not blank the ones
# a KeyCRM read supplied.
_KEEP_BEST = ("external_id", "order_name")

# SQLite tolerates '' in an integer column; Postgres does not. The SQLite
# implementation leans on that with `row.get(col, "")`, so the same rows need a
# typed default here or the first order with no status_group_id fails the insert.
_EMPTY: dict[str, Any] = {
    "source_rank": 0, "status_group_id": 0, "grand_total": 0.0,
    "ordered_at": None,
}


def _build_upsert() -> str:
    cols = ", ".join(_ORDER_COLUMNS)
    placeholders = ", ".join(f"${i}" for i in range(1, len(_ORDER_COLUMNS) + 1))
    sets = [f"{c} = EXCLUDED.{c}" for c in _OVERWRITE]
    sets += [f"{c} = CASE WHEN EXCLUDED.{c} <> '' THEN EXCLUDED.{c} ELSE orders.{c} END"
             for c in _KEEP_BEST]
    sets.append("source_rank = EXCLUDED.source_rank")
    sets.append("synced_at = now()")
    return (
        f"INSERT INTO orders ({cols}, synced_at) "
        f"VALUES ({placeholders}, now()) "
        # The WHERE repeats ux_orders_merge's predicate. Postgres needs it to
        # pick the partial index as the conflict target, exactly as SQLite does.
        f"ON CONFLICT (user_id, merge_key) WHERE merge_key <> '' "
        f"DO UPDATE SET " + ", ".join(sets)
    )


_UPSERT_SQL = _build_upsert()


def _as_timestamptz(value: Any) -> datetime | None:
    """ISO string to an aware datetime, because timestamptz will not take text.

    Naive input is read as UTC. Revision 001 chose "timestamptz, stored in UTC"
    and called the migration the one cheap moment to fix time; what it does not
    say is which zone the naive strings coming off the CRM are in. Assumed here
    and pinned by a test, so that the data transfer (point 4) applies the same
    rule to history rather than a different one by accident.
    """
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


class _BoundToUnit:
    """Holds the unit, not a connection — so the repositories can exist from
    construction, as the port declares them, while the connection they use only
    exists inside the block.

    Found by the conformance test: `UnitOfWork` names `orders` and `users` as
    attributes, and an implementation that only created them in `__aenter__`
    failed `isinstance` before entry. Building them here and resolving the
    connection at call time satisfies the port and turns "used outside a
    transaction" into a sentence rather than an AttributeError.
    """

    def __init__(self, uow: "SqlUnitOfWork") -> None:
        self._uow = uow

    @property
    def _conn(self) -> asyncpg.Connection:
        conn = self._uow._conn
        if conn is None:
            raise RuntimeError(
                "repository used outside `async with` — there is no transaction "
                "to write in, and a write here would not be rolled back by "
                "anything"
            )
        return conn


class PgOrderCache(_BoundToUnit):

    async def upsert(self, user_id: int, rows: list[dict]) -> None:
        if not rows:
            return
        values = []
        for order in rows:
            row = dict(order)
            # Derived here and nowhere else, from the same domain functions the
            # SQLite implementation uses. If these two ever disagree the same
            # physical order lands on two rows under one engine and one under
            # the other, and no test that looks at a single engine would see it.
            row["merge_key"] = merge_key(
                row.get("source", ""), str(row.get("source_order_id", "")),
                row.get("external_id") or None,
            )
            row["source_rank"] = source_rank(row.get("source", ""))
            # The caller's user_id wins over anything in the row: the rows carry
            # `chat_id` from the SQLite era, and under Postgres that is not a
            # column at all.
            row["user_id"] = user_id
            row["ordered_at"] = _as_timestamptz(row.get("ordered_at"))
            values.append(tuple(
                row.get(c, _EMPTY.get(c, "")) if row.get(c) not in (None, "")
                else _EMPTY.get(c, "")
                for c in _ORDER_COLUMNS
            ))
        await self._conn.executemany(_UPSERT_SQL, values)


class PgUserProfiles(_BoundToUnit):

    async def bind_phone(self, chat_id: int, phone: VerifiedPhone) -> int:
        """Create or re-verify the person behind this chat; return their id.

        Keyed on `tg_chat_id`, which mirrors what SQLite's INSERT OR REPLACE
        does today. The other unique column, `phone_normalized`, is therefore
        left to raise: a number arriving from a *different* chat means the
        person changed Telegram account, and moving them is a linking decision
        with rules of its own (§4.8) rather than something an upsert should
        infer. It raises here where SQLite would silently have created a second
        person holding the same number — louder is the improvement, and the
        behaviour is pinned by a test rather than left to be discovered.
        """
        row = await self._conn.fetchrow(
            "INSERT INTO users (tg_chat_id, phone_normalized, updated_at) "
            "VALUES ($1, $2, now()) "
            "ON CONFLICT (tg_chat_id) DO UPDATE "
            "   SET phone_normalized = EXCLUDED.phone_normalized, updated_at = now() "
            "RETURNING id",
            chat_id, phone.e164,
        )
        return int(row["id"])

    async def update_profile(
        self,
        user_id: int,
        *,
        full_name: str | None = None,
        email: str | None = None,
    ) -> None:
        """A plain UPDATE of two columns — no read-back, unlike the SQLite side.

        There the underlying function is an INSERT OR REPLACE that needs the
        whole row, so enrichment first had to fetch the number back. Here the
        columns are addressed directly and the phone is untouchable because it
        is not in the statement.

        A user who does not exist is left alone rather than created: UPDATE
        matching no row is the same "creates nobody" the SQLite implementation
        arranges by checking first.
        """
        await self._conn.execute(
            "UPDATE users SET "
            "  full_name = COALESCE($2, full_name), "
            "  email     = COALESCE($3, email), "
            "  updated_at = now() "
            "WHERE id = $1",
            user_id, full_name, email,
        )


class SqlUnitOfWork:
    """One transaction on one connection, with the identity set on it.

    Acquired on entry, released on exit, and the transaction is rolled back
    unless commit() was called — the default is the safe one because the path
    that forgets is always the exceptional path.
    """

    def __init__(self, pool: asyncpg.Pool, *, user_id: int | None = None) -> None:
        self._pool = pool
        self.user_id = user_id
        self._conn: asyncpg.Connection | None = None
        self._tx: Any = None
        self._committed = False
        # Built here, not in __aenter__: the port declares them as attributes of
        # the unit, and the conformance test checks exactly that.
        self.orders = PgOrderCache(self)
        self.users = PgUserProfiles(self)

    async def __aenter__(self) -> "SqlUnitOfWork":
        self._conn = await self._pool.acquire()
        self._tx = self._conn.transaction()
        await self._tx.start()
        if self.user_id is not None:
            # Transaction-local: it dies with the transaction, so a pooled
            # connection cannot carry one person's identity into the next
            # borrower's request. `true` is what makes it LOCAL.
            await self._conn.execute(
                "SELECT set_config('app.user_id', $1, true)", str(self.user_id),
            )
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        try:
            if not self._committed:
                await self._tx.rollback()
        finally:
            await self._pool.release(self._conn)
            self._conn = None
            self._tx = None

    async def commit(self) -> None:
        await self._tx.commit()
        self._committed = True


def unit_of_work_factory(pool: asyncpg.Pool):
    """Bind a pool once, hand out units — matches UnitOfWorkFactory's shape."""
    def make(*, user_id: int | None = None) -> SqlUnitOfWork:
        return SqlUnitOfWork(pool, user_id=user_id)
    return make
