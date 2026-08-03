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
    """The people the bot knows about.

    Two operations, not one, and the split is the point. Binding a number
    decides whose orders a chat can see; filling in a name does not. Merging
    them into one `save(user_id, phone, name, email)` was the first shape of
    this port, and it forced the caller that only had a name to produce a phone
    — which, since the phone must be a VerifiedPhone, meant either wrapping a
    string from the database back into a proof it no longer had, or weakening
    the type for everybody. Two methods cost one line and keep the guarantee
    where it means something.
    """

    async def bind_phone(self, user_id: int, phone: VerifiedPhone) -> None:
        """Attach an ownership-verified number to a user.

        The only write that changes who a chat is. VerifiedPhone all the way
        down to the repository on purpose: this is the last place the proof
        still exists, and the write it guards is the one that would hand a
        stranger somebody else's order history.
        """
        ...

    async def update_profile(
        self,
        user_id: int,
        *,
        full_name: str | None = None,
        email: str | None = None,
    ) -> None:
        """Fill in what the CRM knows about an already-bound user.

        Best-effort enrichment, and it must never be able to change the phone —
        which is why the phone is not a parameter rather than being an optional
        one nobody passes.
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
