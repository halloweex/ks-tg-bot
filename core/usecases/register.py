"""Everything registration does to the data, and nothing it does to the screen.

The phone arrives as a `VerifiedPhone`, which is the one invariant this project
cannot afford to lose: without ownership verification anyone could bind a
stranger's number to their own chat and read that person's orders and delivery
address. The type is what enforces it now — a caller holding a plain string
cannot reach this function, and the check that mints the type lives in
core.domain.phone, not in whoever remembered to call it.

Both steps are best-effort by design. A customer who shared their contact is
registered even if KeyCRM is down; they will see an empty list and the next
refresh fills it in. Failing registration instead would mean asking them for the
number again, which is the one thing the flow must never do.

**The proof now reaches storage.** `phone.e164` used to be unwrapped at the top
of this function and a plain string went down to `save_user`; `bind_phone` takes
the `VerifiedPhone` itself, so the last place the guarantee still exists is the
write it guards. The string is still unwrapped here, but only for the CRM — an
HTTP client has no use for a proof, and it is not the call that could hand a
stranger somebody else's history.

**This is the registration the UnitOfWork was designed for.** Its docstring says
so out loud: binding a number and writing the orders behind it must land
together, and a crash between them leaves a customer registered with an empty
history. Under the shim that is still what happens — `core/repos/uow.py` is
honest about buying order rather than atomicity — but the scenario now states
the grouping, so the fix arrives with the engine rather than with another edit
here.

**The unit is opened for nobody, and that is the correct reading.** `user_id` is
the service context, and registration is the one flow where there genuinely is
no user yet: the person does not exist until `bind_phone` creates them, inside
the transaction. Every write after that uses the id it returned, which is also
why this scenario — unlike `sync_orders` — never has to guess that a chat id
will do.
"""
from __future__ import annotations

from loguru import logger

from core.domain.order import order_row
from core.domain.phone import VerifiedPhone
from core.ports.crm import BuyerLookup, OrderSource
from core.ports.repositories import CustomerDirectory, UnitOfWork, UnitOfWorkFactory


async def _sync_orders(
    chat_id: int,
    user_id: int,
    phone: str,
    keycrm: OrderSource | None,
    directory: CustomerDirectory,
    uow: UnitOfWork,
) -> None:
    """Fetch this number's orders and cache them (best-effort).

    Deliberately not core.usecases.sync_orders, which is the same scenario plus
    a buyer-profile write. Registration already fetches the profile separately
    and one line earlier; routing it through the other function would give this
    flow a second write it does not currently do. Merging the two is a change in
    behaviour and waits for someone to decide it is the right one — see
    docs/move-status.md.

    Shopify used to be asked here too, in parallel. It left the write path with
    §4.4: KeyCRM already mirrors its orders, and the second writer only bought a
    conflict rule that could not be exercised.
    """
    if not keycrm:
        return

    orders = await keycrm.get_orders_by_phone(phone)
    if not orders:
        return

    # Which CRM buyer cards this number is. Only a by-number request can answer
    # that, and this is the first one a customer ever causes — the window sweep
    # sees orders by card and would otherwise never recognise them.
    #
    # Outside the unit even though it is called from inside the block: the
    # directory is not one of the unit's two repositories, the map is additive
    # and idempotent, and a crash after it costs one more lookup rather than a
    # wrong answer.
    await directory.remember(chat_id, {o.buyer_id for o in orders})
    await uow.orders.upsert(user_id, [order_row(o, chat_id) for o in orders])


async def register_customer(
    chat_id: int,
    phone: VerifiedPhone,
    keycrm: (OrderSource | BuyerLookup) | None,
    directory: CustomerDirectory,
    unit: UnitOfWorkFactory,
    *,
    source: str = "",
) -> None:
    """Bind the verified number to the chat, then fill the cache behind it."""
    # Unwrapped once, here, and only for the CRM: an HTTP client has no use for
    # a proof of ownership. The write that does keep it is bind_phone, which
    # takes the VerifiedPhone itself.
    number = phone.e164

    async with unit() as uow:
        user_id = await uow.users.bind_phone(chat_id, phone, source=source)

        # Enrich profile with KeyCRM buyer data (best-effort)
        if keycrm:
            try:
                buyer = await keycrm.get_buyer_by_phone(number)
                if buyer:
                    await uow.users.update_profile(
                        user_id,
                        full_name=buyer["full_name"], email=buyer["email"],
                    )
            except Exception:
                logger.debug("Buyer profile sync failed for {}", phone)

        # Sync orders into local cache (best-effort, don't block onboarding)
        try:
            await _sync_orders(chat_id, user_id, number, keycrm, directory, uow)
        except Exception:
            logger.debug("Order sync on registration failed for {}", phone)

        await uow.commit()
