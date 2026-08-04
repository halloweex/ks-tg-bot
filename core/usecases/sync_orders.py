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

What is still direct is the database: repositories are imported rather than
passed in. A repository port means a UnitOfWork, and that is a Postgres-shaped
question (`SET LOCAL app.user_id`, transactions spanning several repos) worth
answering against the real engine — docs/move-status.md.
"""
from __future__ import annotations

from core.domain.order import order_row
from core.ports.crm import OrderSource
from core.repos.orders import upsert_orders
from core.repos.users import remember_crm_buyers, save_user


async def sync_orders(chat_id: int, phone: str, keycrm: OrderSource) -> None:
    """Ask the CRM for this number's orders and write what came back.

    Never raises on the CRM being unavailable, because the adapter does not:
    get_orders_by_phone returns the pages it managed. A short read costs
    freshness and nothing else — orders are upserted, never replaced.
    """
    orders = await keycrm.get_orders_by_phone(phone)
    if not orders:
        return

    # This request is the only place that knows which CRM buyer cards this
    # number resolves to — the CRM matched them, we did not. Recording it here
    # is what lets the window sweep, which sees orders by card and never by
    # number, route them to this chat.
    await remember_crm_buyers(chat_id, {o.buyer_id for o in orders})

    # Silent buyer profile refresh
    first = orders[0]
    if first.buyer_name or first.buyer_email:
        try:
            await save_user(
                chat_id, phone,
                full_name=first.buyer_name or None,
                email=first.buyer_email or None,
            )
        except Exception:  # noqa: BLE001
            pass

    await upsert_orders(chat_id, [order_row(o, chat_id) for o in orders])
