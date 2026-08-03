"""What a scenario needs from storage, expressed without naming an engine.

The debt stage 2 left on purpose. Usecases talk to repositories directly today,
which is why `core.usecases` is still outside the independence contract, and the
reason it waited is that a repository port is a UnitOfWork — a transaction and
`SET LOCAL app.user_id` — and both are Postgres-shaped questions that would have
been answered blind against SQLite.

**Why a UnitOfWork rather than one port per repository.** Two writes that must
land together are the whole reason: registration binds a number to a chat and
then writes the orders behind it, and a crash between them today leaves a
customer registered with an empty history. One repository per port would give
each its own connection and its own commit, which is exactly the shape that
cannot express "both or neither".

**Why `user_id` sits on the unit and not on every method.** It is what Postgres
turns into `SET LOCAL app.user_id` at the start of the transaction, and RLS
policies read it from there. Passing it per call would mean every method could
disagree with every other, and the one that forgot would be the one that
returned somebody else's rows. `None` is the worker's service context: no user,
no policy filtering, and deliberately hard to write by accident.
"""
from __future__ import annotations

from types import TracebackType
from typing import Protocol, runtime_checkable

from core.domain.phone import VerifiedPhone


@runtime_checkable
class OrderCache(Protocol):
    """The orders a customer can be shown."""

    async def upsert(self, user_id: int, rows: list[dict]) -> None:
        """Write orders, keeping the more authoritative source's values.

        Rows come from core.domain.order_row. merge_key and source_rank are
        derived by the implementation and never accepted from a caller — that
        is how unrelated orders once collapsed onto one row.
        """
        ...


@runtime_checkable
class UserProfiles(Protocol):
    """The people the bot knows about."""

    async def save(
        self,
        user_id: int,
        phone: VerifiedPhone,
        *,
        full_name: str | None = None,
        email: str | None = None,
    ) -> None:
        """Bind a verified number to a user, optionally with profile fields.

        The phone is a VerifiedPhone rather than a string all the way down to
        the repository on purpose: this is the write that decides whose orders
        a chat can see, and it is the last place where the proof still exists.
        """
        ...


@runtime_checkable
class UnitOfWork(Protocol):
    """One transaction, and the repositories that share it.

    Used as an async context manager. Leaving the block without commit() rolls
    back — the default has to be the safe one, because the path that forgets is
    always the exceptional path.
    """

    orders: OrderCache
    users: UserProfiles

    async def __aenter__(self) -> "UnitOfWork": ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None: ...

    async def commit(self) -> None: ...


class UnitOfWorkFactory(Protocol):
    """How a scenario gets a unit without knowing which engine is under it.

    `user_id=None` is the service context — the worker syncing orders for people
    who have not registered — and it is spelled explicitly so that reaching it
    is a decision rather than an omission.
    """

    def __call__(self, *, user_id: int | None = None) -> UnitOfWork: ...
