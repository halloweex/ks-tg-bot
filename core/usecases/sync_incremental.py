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

**Matching is by CRM buyer card, and the number is only the second rule.** The
first version matched on the number alone and wrote nothing for the one real
customer in production, silently: a buyer card holds several numbers, an order
carries one of them, and the CRM's by-number search matches all of them — so the
customer who shared number A with Telegram had 25 orders, every one of them
found by their number and not one of them carrying it. Which cards a chat is can
only be learned by asking the CRM by number, so the paths that already do —
registration, the orders screen, and `resolve_unknown_buyers` below for anyone
who registered before any of it — record the answer.

The number survives as the second rule for what the card cannot cover: a new
buyer card created for a number we know, which no by-number request has been
made against yet. Both sides go through `normalize_phone`, because Telegram
hands over `+380671234567` and the CRM stores whatever a manager typed, and a
number that does not normalize matches nobody rather than matching loosely.

**The service context, at last used for what it was written for.** The
UnitOfWork port describes `user_id=None` as "the worker syncing orders for
people who have not registered", and this is that worker: the window belongs to
nobody in particular, and the rows it writes are routed to whichever chats they
turn out to match. Every other scenario opens a unit for a person.

**The cursor is deliberately outside that unit.** It has to be: the promise a
cursor makes is that everything up to it has been read, so it is written after
the orders and never with them. A failure that rolls the orders back must leave
the cursor exactly where it was rather than roll it back to a value that was
already behind — and the failure is recorded outside the block for the same
reason, because a record of what went wrong is worthless if it disappears with
the transaction that went wrong.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from loguru import logger

from core.domain.order import order_row
from core.domain.phone import normalize_phone
from core.domain.sync import SyncState, write_stamp
from core.ports.crm import ChangedOrderFeed, OrderSource
from core.ports.repositories import (CustomerDirectory, SyncJournal,
                                     UnitOfWorkFactory)

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

# How many unrecognised chats one sweep is allowed to look up by number. Each is
# a paged request, and the backlog only ever holds people who registered before
# the map existed — registration fills it in for everyone since.
_RESOLVE_PER_SWEEP = 5

# How long an unproductive lookup counts as a lookup. Somebody who has never
# ordered has no buyer card to find, so without this they would be asked about
# on every sweep for as long as they stay registered — two of the three people
# in production are exactly that. A day later they are asked again, because
# "has never ordered" is a state customers leave.
#
# Here rather than in storage since commit 21: how often this sweep spends a
# request is the sweep's policy, and the port refuses a default for that reason.
# The value is the one the repository used to default to, so nothing changed.
RETRY_AFTER_HOURS = 24

# The timestamp format lives in core.domain.sync since commit 20, and since 21
# the only thing this module formats a moment for is the CRM's changed-orders
# filter — never a column. What stays here is the reason there is no clock
# parameter on the repository: the row is stamped by the database and read back
# against the process clock, and those are the same UTC clock everywhere this
# runs. The only place the difference is visible is a test pretending today is
# another day.


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


def plan_window(state: SyncState | None,
                now: datetime) -> tuple[datetime, datetime, bool]:
    """Where this sweep reads from, to, and whether it is a reconciliation.

    Three cases, and the first two are the same window for different reasons:

    * never swept — read the reconciliation window, because a cursor that starts
      at "now" would mean the orders of everyone who registered before the sync
      existed are never fetched by it;
    * a week since the last full pass — §5.3;
    * otherwise, from the cursor minus the overlap.

    Takes moments, not strings, since commit 21. A cursor too damaged to parse
    arrives here as None and lands in the first case — the widest window — which
    is where it landed before, except that the deciding is now done once, by
    whoever stored it, instead of by every reader.
    """
    cursor = state.cursor if state else None
    last_full = state.last_full_at if state else None
    full = cursor is None or last_full is None or now - last_full >= RECONCILE_EVERY
    start = now - RECONCILE_WINDOW if full else cursor - OVERLAP
    return start, now, full


def route(
    orders,
    phones: list[tuple[int, str]],
    buyers: list[tuple[int, str]] | None = None,
) -> dict[int, list[dict]]:
    """Orders grouped into the chats they belong to.

    **By CRM buyer card first, and that is not an optimisation.** The number on
    an order is one of the numbers its buyer card holds, while the CRM's
    by-number search matches all of them — so a customer who shared number A
    with Telegram routinely has orders whose `buyer_phone` reads B. Measured on
    production the day this shipped: the only registered customer there had 25
    orders, every one of them found by their number through the search and none
    of them matching it in the payload. Routing by number alone wrote nothing
    for them, silently, while looking healthy.

    The number is kept as the second rule, for the case the card cannot cover:
    a brand new buyer card created for a number we already know, which no
    by-number request has been made against yet.

    A chat matching twice gets the order once. Two chats matching one order both
    get it — a household or a second Telegram account, which the schema supports
    on purpose and which already see the same orders through the on-demand
    refresh.

    The first list was called `directory` until commit 22, when the module also
    gained a `directory: CustomerDirectory` port and one word started meaning
    two things a screen apart. It is a list of numbers, so it is `phones`.
    """
    chats_by_number: dict[str, list[int]] = {}
    for chat_id, phone in phones:
        number = normalize_phone(phone)
        if number:
            chats_by_number.setdefault(number, []).append(chat_id)

    chats_by_buyer: dict[str, list[int]] = {}
    for chat_id, buyer_id in buyers or ():
        if buyer_id:
            chats_by_buyer.setdefault(str(buyer_id), []).append(chat_id)

    rows: dict[int, list[dict]] = {}
    for order in orders:
        number = normalize_phone(order.buyer_phone)
        matched = set(chats_by_buyer.get(str(order.buyer_id), ()))
        if number:
            matched |= set(chats_by_number.get(number, ()))
        for chat_id in sorted(matched):
            rows.setdefault(chat_id, []).append(order_row(order, chat_id))
    return rows


async def resolve_unknown_buyers(
    lookup: OrderSource,
    limit: int,
    directory: CustomerDirectory,
    unit: UnitOfWorkFactory,
) -> int:
    """Ask the CRM who the chats it does not recognise are. Returns how many.

    A registered customer is invisible to the sweep until something has asked
    the CRM by their number, because only that answer says which buyer cards
    they are. Registration does it and the orders screen does it, so this is for
    everyone who registered before any of it existed — and it does the asking
    through the ordinary by-number scenario, which records the cards and
    refreshes the orders in the same request.

    Bounded per sweep because each one is a paged request, and best-effort
    because it is not what the sweep is for: a customer who cannot be resolved
    now is resolved next time, or the moment they open the bot. Asked once a day
    at most — the answer for somebody who has never ordered is "no card", and
    that is an answer, not a reason to ask again in two minutes.
    """
    from core.usecases.sync_orders import sync_orders

    # Still sliced here rather than asked for by the query. The port answers the
    # whole list on purpose — a LIMIT with no ORDER BY would hand back a
    # different, arbitrary subset, and a sweep quietly asking about different
    # people every round is not something to introduce inside a move.
    waiting = await directory.unidentified(retry_after_hours=RETRY_AFTER_HOURS)

    asked = 0
    for chat_id, phone in waiting[:limit]:
        try:
            await sync_orders(chat_id, phone, lookup, directory, unit)
            # After the call and whatever it found. A customer the CRM has never
            # heard of has no card to record, and without this they would be
            # looked up again in two minutes, and again, for as long as they
            # stay registered.
            await directory.mark_asked(chat_id)
            asked += 1
        except Exception as exc:  # noqa: BLE001 — never costs the sweep its window
            logger.warning("Could not resolve the CRM buyer for chat {}: {}",
                           chat_id, exc)
    if asked:
        logger.info("Sync: asked the CRM about {} unrecognised chat(s)", asked)
    return asked


async def sync_changed_orders(
    crm: ChangedOrderFeed,
    journal: SyncJournal,
    directory: CustomerDirectory,
    unit: UnitOfWorkFactory,
    *,
    lookup: OrderSource | None = None,
    now: datetime | None = None,
) -> SweepResult:
    """Read what changed, write what belongs to somebody, then move the cursor.

    `lookup` is the same CRM asked the other way round — by number — and is used
    only to learn who a chat is when nothing has asked yet. Optional so the
    sweep can be tested, and read before the window rather than after, so a chat
    resolved this round is routed this round.

    Raises whatever stopped it, after recording it. The caller decides what a
    failed sweep means — here it means only that the cursor stays put and the
    next sweep covers this window again.
    """
    now = now or datetime.now(timezone.utc)
    state = await journal.state(SOURCE)
    start, end, full = plan_window(state, now)

    if lookup is not None:
        await resolve_unknown_buyers(lookup, _RESOLVE_PER_SWEEP, directory, unit)

    await journal.begin(SOURCE)
    try:
        orders = await crm.get_orders_changed_between(write_stamp(start), write_stamp(end))
        rows = route(orders, await directory.phones(), await directory.buyers())
        # The service context the UnitOfWork port describes: this window belongs
        # to nobody in particular, and the rows go to whichever chats they turn
        # out to match.
        async with unit() as uow:
            for chat_id, chat_rows in rows.items():
                await uow.orders.upsert(chat_id, chat_rows)
            await uow.commit()
    except Exception as exc:  # noqa: BLE001 — recorded, then re-raised as it is
        # Outside the block, and on purpose: a record of what went wrong is
        # worth nothing if it disappears with the transaction that went wrong.
        await journal.failed(SOURCE, f"{type(exc).__name__}: {exc}")
        raise

    # Last, and only here: the cursor is the promise that everything up to this
    # moment has been read, and it must be made after the writes rather than
    # alongside them. A moment goes across, not a string — the spelling on disk
    # belongs to whoever stores it.
    await journal.finished(SOURCE, end, full=full)

    written = sum(len(chat_rows) for chat_rows in rows.values())
    logger.info(
        "Sync{}: {} order(s) changed since {}, {} written to {} chat(s)",
        " (full)" if full else "", len(orders), write_stamp(start), written, len(rows),
    )
    return SweepResult(
        window_from=write_stamp(start), window_to=write_stamp(end), full=full,
        fetched=len(orders), written=written,
    )
