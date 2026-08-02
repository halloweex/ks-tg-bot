"""Fetch a customer's orders from every source and cache them.

The first scenario to leave a handler. It was 45 lines in the middle of
bot/handlers/orders.py, between the message formatting and the keyboards, and
that is where it broke: for a day it raised TypeError before reaching the
network and nothing noticed, because there was nothing that could test it
without also building a Telegram message (docs/found-during-move.md §13).

It no longer names a shop system. Both sources arrive as `OrderSource`, both
answer with domain `Order`s, and the row they become is built once in the
domain — so adding a third source is an adapter and a line at the entry point,
not an edit here.

What is still direct is the database: repositories are imported rather than
passed in. A repository port means a UnitOfWork, and that is a Postgres-shaped
question (`SET LOCAL app.user_id`, transactions spanning several repos) worth
answering against the real engine — docs/move-status.md.
"""
from __future__ import annotations

import asyncio

from core.domain.order import order_row
from core.ports.crm import OrderSource
from core.repos.orders import upsert_orders
from core.repos.users import save_user


async def sync_orders(
    chat_id: int,
    phone: str,
    keycrm: OrderSource,
    shopify: OrderSource | None,
) -> None:
    """Ask both sources at once, write whatever came back.

    Neither source is allowed to cost the other: they are gathered with
    return_exceptions, so a KeyCRM outage still leaves the customer their
    Shopify orders and the other way round.
    """
    async def _empty_list() -> list:
        return []

    coros = [keycrm.get_orders_by_phone(phone)]
    if shopify is not None:
        coros.append(shopify.get_orders_by_phone(phone))
    else:
        coros.append(_empty_list())

    results = await asyncio.gather(*coros, return_exceptions=True)
    keycrm_result = results[0]
    shopify_result = results[1]

    db_rows: list[dict] = []

    if not isinstance(keycrm_result, Exception):
        db_rows.extend(order_row(o, chat_id) for o in keycrm_result)
        # Silent buyer profile refresh
        if keycrm_result:
            first = keycrm_result[0]
            if first.buyer_name or first.buyer_email:
                try:
                    await save_user(
                        chat_id, phone,
                        full_name=first.buyer_name or None,
                        email=first.buyer_email or None,
                    )
                except Exception:  # noqa: BLE001
                    pass

    if not isinstance(shopify_result, Exception):
        # No filtering against the KeyCRM ids any more: both sources write the
        # same merge_key for the same physical order, so the unique index keeps
        # one row and the priority in the upsert decides whose values it holds.
        db_rows.extend(order_row(o, chat_id) for o in shopify_result)

    if db_rows:
        await upsert_orders(chat_id, db_rows)
