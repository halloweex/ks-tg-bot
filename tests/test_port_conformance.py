"""Every implementation of a port matches that port's signatures, not just its
method names.

This exists because moving the scenarios onto ports blinds the check that used
to cover them. tests/test_call_arity.py says so in its own docstring: "Calls
through an attribute ... are all skipped". Every step of the port migration
turns a plain call into a call through an attribute — `upsert_orders(rows)`
becomes `uow.orders.upsert(user_id, rows)` — so the net that caught §13 of
docs/found-during-move.md (four parameters declared, three passed, TypeError in
production for a day) stops seeing the calls it was written for.

`isinstance` against a `runtime_checkable` Protocol does not fill the gap: it
checks that the names exist and nothing about what they take. An implementation
whose method quietly grew a required argument satisfies `isinstance` and fails
at the first call, which is the same failure §13 was.

So this compares signatures. Parameter names, kinds and defaults — not
annotations, because a port writing `dict[str, Offer]` and an implementation
writing `dict` are the same contract, while a port promising a keyword the
implementation does not accept is not.
"""
from __future__ import annotations

import inspect

import pytest

from core.ports.analytics import UsageStats
from core.ports.outbox import MessageQueue, PendingMessages
from core.ports.repositories import (OfferCache, OrderCache, RestockWatchlist,
                                     StockSnapshot, UnitOfWork, UserProfiles)
from core.repos.catalogue import SqliteOfferCache
from core.repos.events import SqliteUsageStats
from core.repos.stock import SqliteRestockWatchlist, SqliteStockSnapshot
from core.ports.users import KnownBirthdays, LanguageChoice, MailingList
from core.repos.outbox import SqliteMessageQueue, SqlitePendingMessages
from core.repos.users import (SqliteKnownBirthdays, SqliteLanguageChoice,
                             SqliteMailingList)
from core.repos.pg import PgOrderCache, PgUserProfiles, SqlUnitOfWork
from core.repos.uow import SqliteOrderCache, SqliteUnitOfWork, SqliteUserProfiles

# The dunders a port may legitimately promise. Everything else beginning with an
# underscore belongs to whoever implements it.
_DUNDERS_THAT_MATTER = {"__aenter__", "__aexit__", "__call__"}

PAIRS = [
    (OrderCache, SqliteOrderCache),
    (OrderCache, PgOrderCache),
    (UserProfiles, SqliteUserProfiles),
    (UserProfiles, PgUserProfiles),
    (UnitOfWork, SqliteUnitOfWork),
    (UnitOfWork, SqlUnitOfWork),
    (OfferCache, SqliteOfferCache),
    (UsageStats, SqliteUsageStats),
    (MessageQueue, SqliteMessageQueue),
    (PendingMessages, SqlitePendingMessages),
    (LanguageChoice, SqliteLanguageChoice),
    (MailingList, SqliteMailingList),
    (KnownBirthdays, SqliteKnownBirthdays),
    (StockSnapshot, SqliteStockSnapshot),
    (RestockWatchlist, SqliteRestockWatchlist),
]


def _declared(cls) -> dict[str, inspect.Signature]:
    """The methods a class writes down itself, with their signatures."""
    out: dict[str, inspect.Signature] = {}
    for name, value in vars(cls).items():
        if name.startswith("_") and name not in _DUNDERS_THAT_MATTER:
            continue
        if not inspect.isfunction(value):
            continue
        out[name] = inspect.signature(value)
    return out


def _shape(sig: inspect.Signature) -> list[tuple[str, str, object]]:
    """(name, kind, default) per parameter, `self` dropped, annotations ignored."""
    return [
        (p.name, str(p.kind), p.default)
        for p in sig.parameters.values()
        if p.name != "self"
    ]


@pytest.mark.parametrize(
    "port, implementation",
    PAIRS,
    ids=[f"{port.__name__}<-{impl.__name__}" for port, impl in PAIRS],
)
def test_the_implementation_takes_what_the_port_promises(port, implementation):
    for name, port_signature in _declared(port).items():
        found = getattr(implementation, name, None)
        assert found is not None, (
            f"{implementation.__name__} does not implement {port.__name__}.{name}"
        )
        assert _shape(inspect.signature(found)) == _shape(port_signature), (
            f"{implementation.__name__}.{name} does not take what "
            f"{port.__name__}.{name} promises"
        )


def test_every_port_in_the_migration_is_covered_here():
    """A port with no pair is a port nothing is checked against.

    Cheap and worth it: the failure mode of this whole file is somebody adding a
    seventh port, wiring it up, and never noticing that the row protecting it
    was not added. The list below is the one thing that must be edited by hand,
    so it is the one thing stated out loud.
    """
    covered = {port for port, _ in PAIRS}
    assert covered == {OrderCache, UserProfiles, UnitOfWork, OfferCache, UsageStats,
                       MessageQueue, PendingMessages, LanguageChoice, MailingList,
                       KnownBirthdays, StockSnapshot, RestockWatchlist}
