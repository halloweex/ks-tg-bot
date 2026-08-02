"""`/demo` — seed fake orders into the caller's own cache, for testing the UI.

Deliberately *not* written to KeyCRM: that is the live CRM the business runs on
and every number we reason about comes out of it. These rows live only in the
bot's local cache, only under the admin's own chat_id, so no customer can ever
be shown one, and `/demo clear` removes them exactly.

The fixture is chosen to exercise the things that are awkward to hit by hand:
a long order that has to collapse, a cancelled order that must stay out of
delivery tracking, a website order with a store number, and a product bought
three times so favourites has something to rank.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message
from loguru import logger

from core.config import AppConfig
from bot.db import (DEMO_SOURCE, count_demo_orders, delete_demo_orders,
                    get_user_phone, upsert_orders)
router = Router()

# Real catalogue names: the point of the fixture is to see the real rendering,
# and these run to 100+ characters, which is what shortening has to survive.
_SERUM = "LALARECIPE Hempseed Serum Заспокійлива сироватка з конопляною олією, 50 мл"
_MASK = "LALARECIPE PDRN Melting Gel Mask - Гелева маска з PDRN комплексом, 90 мл"
_CREAM = "NEOGEN DERMALOGY High R Exosome Cream Відновлювальний крем з екзосомами Cica, 40 мл"
_TONER = "NARD Deep Moisture Pore Toner - Тонер для обличчя зволожуючий, 500 мл"
_SUN = "Abib Quick Sunstick Protection Bar - SPF50+ сонцезахисний стік, 22 г"
_EYE = "Differ & Deeper Антивіковий крем для шкіри навколо очей з пептидами, 30 мл"
_CLEANSER = "Lalarecipe Matcha PDRN Self-Foaming Pack Cleanser - Пінка для вмивання, 200 мл"


def _fixture(chat_id: int) -> list[dict]:
    """The demo orders, dated backwards from today so ordering looks natural."""
    today = datetime.utcnow()

    def day(n: int) -> str:
        return (today - timedelta(days=n)).strftime("%Y-%m-%dT%H:%M:%S")

    def order(idx, days_ago, status, group, items, *, name="", ttn="", ship=""):
        return {
            "chat_id": chat_id,
            "source": DEMO_SOURCE,
            "source_order_id": f"demo-{idx}",
            "external_id": "",
            "order_name": name,
            "status_name": status,
            "status_group_id": group,
            "grand_total": sum(i[1] * i[2] for i in items),
            "currency": "грн",
            "ordered_at": day(days_ago),
            "products_json": json.dumps(
                [{"name": n, "qty": q, "sku": s} for n, q, _price, s in items],
                ensure_ascii=False,
            ),
            "buyer_name": "",
            "payment_status": "paid",
            "tracking_code": ttn,
            "shipping_status": ship,
            "delivery_city": "",
            "receive_point": "",
            "recipient_name": "",
        }

    return [
        # In transit right now, from the website, so it carries a store number.
        order(1, 2, "Прибув у відділення", 4,
              [(_SERUM, 1, 1310, "1656"), (_CREAM, 1, 1490, "980")],
              name="#20014", ttn="20451496877635", ship="pickup"),
        # Seven items: forces the collapse/expand button.
        order(2, 21, "completed", 5,
              [(_SERUM, 1, 1310, "1656"), (_MASK, 2, 240, "1401"),
               (_TONER, 1, 590, "770"), (_SUN, 1, 640, "1502"),
               (_EYE, 1, 890, "310"), (_CLEANSER, 1, 520, "1290"),
               (_CREAM, 1, 1490, "980")],
              ttn="20451465340708", ship="delivered"),
        # Repeat of the serum and the mask — gives favourites a real ranking.
        order(3, 64, "completed", 5,
              [(_SERUM, 1, 1310, "1656"), (_MASK, 1, 240, "1401")],
              ttn="20451454103588", ship="delivered"),
        # Cancelled, and it keeps its tracking code: this is the row that must
        # NOT appear under delivery.
        order(4, 96, "canceled", 6,
              [(_TONER, 1, 590, "770")],
              ttn="20451352137090", ship="return"),
        # Third serum purchase, so the top favourite is unambiguous.
        order(5, 140, "completed", 5,
              [(_SERUM, 1, 1310, "1656")],
              name="#19502", ttn="20451312736567", ship="delivered"),
    ]


@router.message(Command("demo"))
async def cmd_demo(
    message: Message,
    command: CommandObject,
    config: AppConfig,
) -> None:
    """Seed or clear demo orders for the caller. Admin only.

    Its replies are English literals rather than entries in the string tables:
    this is an operator surface, it is never shown to a customer, and it is
    never translated — same rule as /stats and the backup alerts.
    """
    chat_id = message.from_user.id
    if chat_id not in config.env.admin_ids:
        return

    if (command.args or "").strip().lower() in ("clear", "off", "remove"):
        removed = await delete_demo_orders(chat_id)
        logger.info("Demo orders cleared for chat {} ({} rows)", chat_id, removed)
        await message.answer(f"Demo orders removed: {removed}")
        return

    if not await get_user_phone(chat_id):
        # Every order screen looks the phone up first and bails without one.
        await message.answer(
            "You are not registered yet — send /start and share your contact "
            "first, otherwise the order screens will not open."
        )
        return

    orders = _fixture(chat_id)
    await upsert_orders(chat_id, orders)
    logger.info("Demo orders seeded for chat {} ({} rows)", chat_id, len(orders))

    await message.answer(
        f"Seeded {len(orders)} demo orders in your own cache only — they are not "
        f"in KeyCRM and no customer can see them.\n\n"
        f"They cover: an order in transit, a 7-item order (collapse/expand), "
        f"a cancelled one that must stay out of Delivery, and a product bought "
        f"3× so Favourites has something to rank.\n\n"
        f"Remove with /demo clear. Currently seeded: "
        f"{await count_demo_orders(chat_id)}."
    )
