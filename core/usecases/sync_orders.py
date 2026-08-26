"""Fetch a customer's orders by number and cache them.

The first scenario to leave a handler. It was 45 lines in the middle of
bot/handlers/orders.py, between the message formatting and the keyboards, and
that is where it broke: for a day it raised TypeError before reaching the
network and nothing noticed, because there was nothing that could test it
without also building a Telegram message (docs/found-during-move.md §13).

**One source, since stage 4.** It used to ask KeyCRM and Shopify at once and
merge the answers. Shopify left the write path with §4.4: the incremental sync
reads KeyCRM, and a second writer whose orders KeyCRM already mirrors buys
nothing but a conflict rule nobody can test. The adapter is still there and
still parsed — what changed is that nothing calls it to write an order.

**The first scenario to open a UnitOfWork.** Until this commit nothing in the
tree created one — the port and both implementations existed, and the shim in
core/repos/uow.py said in its own docstring that it buys order rather than
atomicity. That is still exactly what it buys here: the two writes below sit in
one block and still commit separately, because the functions underneath each
open their own connection. What changes is that the scenario now says which
writes belong together, so the day SqlUnitOfWork goes under it, they do.

**`user_id=chat_id`, and that is the deferred confusion, not a slip.** Under
SQLite the surrogate and the chat id are the same number, so this is true today.
Under Postgres it is not, and there is nowhere to ask: the only operation that
hands back a surrogate is `bind_phone`, because it is the only one that can
create a person (see the identity rule in core/ports/users.py). Fixing that is
one of the four items docs/move-status.md defers until after the series.

**What the buyer map is not part of.** `remember` stays outside the block. The
map is additive and idempotent, so a crash after it costs one more lookup rather
than a wrong answer, and it is written first for the same reason it always was —
the sweep that routes by card must be able to find this chat even if everything
after this line fails.

**The profile write stopped carrying a phone, and writes the same one.** It used
to call `save_user(chat_id, phone, …)` with the number this function was handed;
`update_profile` refuses a phone by design, so the adapter reads it back from
the row instead. Same value on every reachable path — both callers take the
number from `users` in the first place, one through `get_user_phone` and one
straight out of `chats_without_crm_buyer`. What the port does change is the
unreachable path: `save_user` would have *created* a row for an unbound chat,
and `update_profile` leaves it alone. Neither caller can reach that, so nothing
observable moves; a write that could invent a user is simply no longer spellable
from here.

**The `except` inside the block is preserved and is a question for later.** The
profile refresh is a nicety and the orders are the reason for the call, so a
failing profile write must not cost them — that is today's behaviour and this
commit keeps it. Under a real transaction, swallowing an error and carrying on
is not free: Postgres marks the transaction aborted and the `upsert` after it
fails too. That is a change to make with SqlUnitOfWork, where it can be tested
against an engine that actually behaves that way.
"""
from __future__ import annotations

from loguru import logger

from core.domain.linking import buyer_cards, shared_by_several_people
from core.domain.order import order_row
from core.ports.crm import OrderSource
from core.ports.repositories import CustomerDirectory, UnitOfWorkFactory


async def sync_orders(
    chat_id: int,
    phone: str,
    keycrm: OrderSource,
    directory: CustomerDirectory,
    unit: UnitOfWorkFactory,
) -> None:
    """Ask the CRM for this number's orders and write what came back.

    Never raises on the CRM being unavailable, because the adapter does not:
    get_orders_by_phone returns the pages it managed. A short read costs
    freshness and nothing else — orders are upserted, never replaced.
    """
    orders = await keycrm.get_orders_by_phone(phone)
    if not orders:
        return

    if shared_by_several_people(orders):
        # §4.8, and the earliest possible moment to stop. Nothing is written:
        # not the orders, and above all not the buyer cards — recording those
        # would attach every one of these people's cards to this chat
        # permanently, and the window sweep routes by card, so the leak would
        # continue on its own after this request is long over.
        #
        # Measured on this CRM: 203 numbers, 180 of them with orders on both
        # sides. Loud rather than silent, because a customer who sees nothing
        # will ask, and the answer is ownership confirmation by a person.
        logger.warning(
            "Refusing to link chat {} by number: the CRM returned {} buyer "
            "cards for it (§4.8), {} order(s) left unattached",
            chat_id, len(buyer_cards(orders)), len(orders),
        )
        # Durable, because the window sweep matches by number every two minutes
        # and would otherwise resume the leak on its own.
        await directory.mark_shared(chat_id)
        return

    # This request is the only place that knows which CRM buyer cards this
    # number resolves to — the CRM matched them, we did not. Recording it here
    # is what lets the window sweep, which sees orders by card and never by
    # number, route them to this chat.
    await directory.remember(chat_id, {o.buyer_id for o in orders})

    async with unit(user_id=chat_id) as uow:
        # Silent buyer profile refresh
        first = orders[0]
        if first.buyer_name or first.buyer_email:
            try:
                await uow.users.update_profile(
                    chat_id,
                    full_name=first.buyer_name or None,
                    email=first.buyer_email or None,
                )
            except Exception:  # noqa: BLE001
                pass

        await uow.orders.upsert(chat_id, [order_row(o, chat_id) for o in orders])
        await uow.commit()
