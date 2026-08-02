"""Everything registration does to the data, and nothing it does to the screen.

The phone arriving here is already ownership-verified. That check reads a
Telegram contact — `contact.user_id` against `from_user.id` — so it stays in the
handler, and it is the one invariant this project cannot afford to lose: without
it anyone could bind a stranger's number to their own chat and read that
person's orders and delivery address.

Both steps are best-effort by design. A customer who shared their contact is
registered even if KeyCRM is down; they will see an empty list and the next
refresh fills it in. Failing registration instead would mean asking them for the
number again, which is the one thing the flow must never do.
"""
from __future__ import annotations

import asyncio

from loguru import logger

from core.domain.order import order_row
from core.ports.crm import BuyerLookup, OrderSource
from core.repos.orders import upsert_orders
from core.repos.users import save_user


async def _sync_orders(
    chat_id: int,
    phone: str,
    keycrm: OrderSource | None,
    shopify: OrderSource | None,
) -> None:
    """Fetch orders from APIs and cache locally (best-effort).

    Deliberately not core.usecases.sync_orders, which is the same scenario plus
    a buyer-profile write. Registration already fetches the profile separately
    and one line earlier; routing it through the other function would give this
    flow a second write it does not currently do. Merging the two is a change in
    behaviour and waits for someone to decide it is the right one — see
    docs/move-status.md.
    """
    coros = []
    if keycrm:
        coros.append(keycrm.get_orders_by_phone(phone))
    if shopify:
        coros.append(shopify.get_orders_by_phone(phone))
    if not coros:
        return

    results = await asyncio.gather(*coros, return_exceptions=True)

    db_rows: list[dict] = []
    idx = 0
    if keycrm:
        if not isinstance(results[idx], Exception):
            db_rows.extend(order_row(o, chat_id) for o in results[idx])
        idx += 1
    if shopify:
        if not isinstance(results[idx], Exception):
            db_rows.extend(order_row(o, chat_id) for o in results[idx])

    if db_rows:
        await upsert_orders(chat_id, db_rows)


async def register_customer(
    chat_id: int,
    phone: str,
    keycrm: (OrderSource | BuyerLookup) | None,
    shopify: OrderSource | None,
) -> None:
    """Bind the verified number to the chat, then fill the cache behind it."""
    await save_user(chat_id, phone)

    # Enrich profile with KeyCRM buyer data (best-effort)
    if keycrm:
        try:
            buyer = await keycrm.get_buyer_by_phone(phone)
            if buyer:
                await save_user(
                    chat_id, phone,
                    full_name=buyer["full_name"], email=buyer["email"],
                )
        except Exception:
            logger.debug("Buyer profile sync failed for {}", phone)

    # Sync orders into local cache (best-effort, don't block onboarding)
    try:
        await _sync_orders(chat_id, phone, keycrm, shopify)
    except Exception:
        logger.debug("Order sync on registration failed for {}", phone)
