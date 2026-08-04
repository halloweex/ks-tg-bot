"""One sweep of the changed-orders window: what §5.2 and §5.3 describe.

The bot reads only from its own database, so this is the only thing that makes
an order in the CRM become an order on a customer's screen. Everything else in
the file follows from that: the window overlaps, the cursor moves only after the
whole window was read, and a sweep that could not finish leaves the cursor
exactly where it was.

**Why a window and not one request per customer.** Asking the CRM for each
registered customer's orders costs one request per customer per cycle and stops
being possible at a few hundred people; asking what changed costs one request
per cycle whatever the number of customers is. It also happens to be the shape
the backfill needs, so stage 5 widens a window rather than writing a second
fetch path.

**Who the orders are written to, and who they are not.** Today an order belongs
to a chat: `orders.chat_id` is not nullable and the unique index is per chat, so
an order whose number matches nobody registered has no row it could occupy. The
sweep therefore keeps what it can place and drops the rest — which for this
business is most of what it reads, because three people have registered and
twenty thousand have bought something. That is the honest boundary of this
stage, not an oversight: §4.4 widens the key and §5.4 gives orders a phone of
their own in the stage that makes `orders.user_id` nullable, and the linking
rules that then decide who an order belongs to are §4.8's, which cost two
hundred and three numbers their automatic link and need the backfill to exist
before they mean anything.

**Matching is by normalized number and nothing else.** Both sides are written by
different systems — Telegram hands over `+380671234567`, the CRM stores whatever
a manager typed — so both go through `normalize_phone`, and a number that does
not normalize matches nobody rather than matching loosely.

Repositories are imported directly here, as in the other two scenarios. The
UnitOfWork port (core/ports/repositories.py) is where this belongs — it names
this exact case as its service context, `user_id=None`, the worker writing for
people who have not registered — and it moves onto it with the second
implementation, which needs the engine that gives `SET LOCAL app.user_id`
something to set.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from loguru import logger

from core.domain.order import order_row
from core.domain.phone import normalize_phone
from core.ports.crm import ChangedOrderFeed
from core.repos.orders import upsert_orders
from core.repos.sync_state import (begin_run, finish_failure, finish_success,
                                   get_state)
from core.repos.users import registered_phones

# The name this integration keeps in sync_state. One source, one row.
SOURCE = "keycrm"

# How far back the window starts before the cursor. §5.2: with a strict `>` the
# records written during the previous pass are lost for good, and the cost of
# the overlap is re-reading a handful of orders that upsert to the same values.
OVERLAP = timedelta(minutes=5)

# §5.3. Re-read the last thirty days in full once a week, ignoring the cursor —
# minutes of work, and the only thing that catches an order the cursor skipped,
# whether through a bug here or a page that shifted under a sweep.
RECONCILE_WINDOW = timedelta(days=30)
RECONCILE_EVERY = timedelta(days=7)

# Both the CRM filter and SQLite's datetime() speak this, in UTC. Which is also
# why there is no clock parameter on the repository: the row is stamped by the
# database and read back against the process clock, and those are the same UTC
# clock everywhere this runs. The only place the difference is visible is a test
# pretending today is another day.
_STAMP = "%Y-%m-%d %H:%M:%S"


@dataclass(frozen=True)
class SweepResult:
    """What one sweep did, for the log line and the tests.

    `fetched` and `written` diverge on purpose and the gap is the interesting
    number: it is how much of the CRM's activity belongs to people who are not
    in the bot yet.
    """

    window_from: str
    window_to: str
    full: bool
    fetched: int
    written: int


def _stamp(moment: datetime) -> str:
    return moment.strftime(_STAMP)


def _read(stamp: str | None) -> datetime | None:
    """A timestamp as stored, or None if it is missing or unreadable.

    Unreadable is treated as missing rather than raised on: the callers use it
    to decide how far back to read, and the safe answer to "I cannot tell" is
    the wider window.
    """
    if not stamp:
        return None
    try:
        return datetime.strptime(stamp, _STAMP).replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def plan_window(state: dict | None, now: datetime) -> tuple[datetime, datetime, bool]:
    """Where this sweep reads from, to, and whether it is a reconciliation.

    Three cases, and the first two are the same window for different reasons:

    * never swept — read the reconciliation window, because a cursor that starts
      at "now" would mean the orders of everyone who registered before the sync
      existed are never fetched by it;
    * a week since the last full pass — §5.3;
    * otherwise, from the cursor minus the overlap.
    """
    cursor = _read((state or {}).get("cursor"))
    last_full = _read((state or {}).get("last_full_at"))
    full = cursor is None or last_full is None or now - last_full >= RECONCILE_EVERY
    start = now - RECONCILE_WINDOW if full else cursor - OVERLAP
    return start, now, full


def route(orders, directory: list[tuple[int, str]]) -> dict[int, list[dict]]:
    """Orders grouped into the chats they belong to, by normalized number.

    A number belonging to two chats writes the order to both. That is not the
    §4.8 collision — it is a household or somebody's second Telegram account,
    both of which the current schema supports on purpose, and both of which
    already see the same orders through the on-demand refresh.
    """
    chats_by_number: dict[str, list[int]] = {}
    for chat_id, phone in directory:
        number = normalize_phone(phone)
        if number:
            chats_by_number.setdefault(number, []).append(chat_id)

    rows: dict[int, list[dict]] = {}
    for order in orders:
        number = normalize_phone(order.buyer_phone)
        if not number:
            continue
        for chat_id in chats_by_number.get(number, ()):
            rows.setdefault(chat_id, []).append(order_row(order, chat_id))
    return rows


async def sync_changed_orders(
    crm: ChangedOrderFeed, *, now: datetime | None = None
) -> SweepResult:
    """Read what changed, write what belongs to somebody, then move the cursor.

    Raises whatever stopped it, after recording it. The caller decides what a
    failed sweep means — here it means only that the cursor stays put and the
    next sweep covers this window again.
    """
    now = now or datetime.now(timezone.utc)
    state = await get_state(SOURCE)
    start, end, full = plan_window(state, now)

    await begin_run(SOURCE)
    try:
        orders = await crm.get_orders_changed_between(_stamp(start), _stamp(end))
        rows = route(orders, await registered_phones())
        for chat_id, chat_rows in rows.items():
            await upsert_orders(chat_id, chat_rows)
    except Exception as exc:  # noqa: BLE001 — recorded, then re-raised as it is
        await finish_failure(SOURCE, f"{type(exc).__name__}: {exc}")
        raise

    # Last, and only here: the cursor is the promise that everything up to this
    # moment has been read, and it must be made after the writes rather than
    # alongside them.
    await finish_success(SOURCE, _stamp(end), full=full)

    written = sum(len(chat_rows) for chat_rows in rows.values())
    logger.info(
        "Sync{}: {} order(s) changed since {}, {} written to {} chat(s)",
        " (full)" if full else "", len(orders), _stamp(start), written, len(rows),
    )
    return SweepResult(
        window_from=_stamp(start), window_to=_stamp(end), full=full,
        fetched=len(orders), written=written,
    )
