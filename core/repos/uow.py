"""A unit of work over the SQLite repositories — the interface, not the atomicity.

Deliberately a shim, and saying so out loud is the point of the module.

The functions it delegates to each open their own connection and commit on their
own (`core/repos/base.py`), so `commit()` here has nothing left to commit and
leaving the block early rolls nothing back. What this buys is order: the
scenarios can be rewired onto the port now, while SQLite is still underneath and
every test is green, and the engine can change afterwards without touching them
again. Two changes, one at a time, instead of a rewrite that swaps the interface
and the database in the same commit.

**What is therefore still true after this exists:** registration that dies
between binding the number and writing the orders still leaves a customer
registered with an empty history. That is not fixed here and must not be
believed to be — the fix arrives with SqlUnitOfWork, where a transaction is real
and `SET LOCAL app.user_id` has something to set it on.
"""
from __future__ import annotations

from types import TracebackType

from core.domain.phone import VerifiedPhone
from core.repos.orders import upsert_orders
from core.repos.users import get_user_phone, save_user


class SqliteOrderCache:
    async def upsert(self, user_id: int, rows: list[dict]) -> None:
        await upsert_orders(user_id, rows)


class SqliteUserProfiles:
    async def bind_phone(self, user_id: int, phone: VerifiedPhone) -> None:
        await save_user(user_id, phone.e164)

    async def update_profile(
        self,
        user_id: int,
        *,
        full_name: str | None = None,
        email: str | None = None,
    ) -> None:
        """Reads the number back before writing, because the function
        underneath is an INSERT OR REPLACE and needs the whole row.

        The extra read is the price of not changing that function during an
        engine migration; on Postgres this becomes a plain UPDATE of two
        columns. A user who is not bound yet is left alone rather than created
        with an empty phone — that row would be a user nobody can be.
        """
        phone = await get_user_phone(user_id)
        if not phone:
            return
        await save_user(user_id, phone, full_name=full_name, email=email)


class SqliteUnitOfWork:
    """Implements core.ports.repositories.UnitOfWork against today's database.

    `user_id` is accepted and ignored: SQLite has no `SET LOCAL` and no row
    level security, so there is nothing to set it on. It is taken anyway,
    because the seam has to exist before the engine that uses it — a parameter
    added later would mean revisiting every call site at the worst moment.
    """

    def __init__(self, *, user_id: int | None = None) -> None:
        self.user_id = user_id
        self.orders = SqliteOrderCache()
        self.users = SqliteUserProfiles()

    async def __aenter__(self) -> "SqliteUnitOfWork":
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        return None

    async def commit(self) -> None:
        """Nothing to do: every write below already committed itself.

        Not raising and not warning either. A scenario written against the port
        must call this, and it has to be free to do so under both engines —
        the difference between the two implementations is what the call means,
        not whether it is allowed.
        """
        return None
