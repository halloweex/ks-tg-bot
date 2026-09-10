"""Delivery screen — Nova Poshta tracking for the customer's parcels."""
from __future__ import annotations

import json
from datetime import datetime
from html import escape

from aiogram.types import InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from core import texts
from bot.callbacks import MenuAction
from core.i18n import Texts
from bot.analytics import track
from core.repos.orders import get_cached_orders, get_orders_with_tracking
from core.repos.users import get_user_phone
from bot.screen import typing
from core.adapters.novaposhta.client import NovaPoshtaClient


def _format_order_label(row: dict, t: Texts) -> str:
    """Short label for an order: source + products summary."""
    label = texts.with_logo(escape(t.order_source_label(row)), texts.INSTAGRAM, "📸")

    try:
        products = json.loads(row.get("products_json", "[]"))
    except (json.JSONDecodeError, TypeError):
        products = []

    if products:
        items = ", ".join(escape(texts.shorten_name(p["name"])) for p in products[:2])
        if len(products) > 2:
            items += f" +{len(products) - 2}"
        label += f" ({items})"

    return label


def _format_date(raw: str) -> str:
    """Try to format a date string to DD.MM.YYYY."""
    if not raw:
        return ""
    for fmt in ("%d-%m-%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%d.%m.%Y %H:%M:%S"):
        try:
            return datetime.strptime(raw, fmt).strftime("%d.%m.%Y")
        except ValueError:
            continue
    return raw


def parcel_lines(row: dict, tracking_info, t: Texts, *,
                 as_html: bool = True) -> list[str]:
    """Where the parcel is, in the carrier's words or the shop's.

    `as_html=False` for a rich block, whose text is structured rather than
    parsed: a branch on вул. В'ячеслава arrives there as `В&#x27;ячеслава`
    and is shown with the entity spelled out, because nothing unescapes it.
    Escaping belongs to the surface that parses HTML, and only one of the two
    does.

    Public because the orders screen shows this under the order it belongs to:
    the two used to be separate menu entries answering what a customer thinks
    of as one question. Nova Poshta is the only source for "where is it right
    now"; the CRM's own shipping status is the fallback for when it cannot be
    reached or has not been asked.
    """
    esc = escape if as_html else (lambda v: v)
    lines: list[str] = []
    if tracking_info:
        ts = tracking_info
        if ts.status:
            lines.append(f"{t.MSG_DELIVERY_STATUS.format(status=esc(t.status(ts.status)))}")
        if ts.warehouse_recipient:
            lines.append(f"{t.MSG_DELIVERY_WAREHOUSE.format(warehouse=esc(ts.warehouse_recipient))}")
        # Handed over beats arrived beats promised: the first of these that is
        # known is the most recent thing that actually happened to the parcel.
        if ts.recipient_date:
            lines.append(f"{t.MSG_DELIVERY_ACTUAL.format(date=_format_date(ts.recipient_date))}")
        elif ts.actual_delivery:
            lines.append(f"{t.MSG_DELIVERY_ARRIVED.format(date=_format_date(ts.actual_delivery))}")
        elif ts.scheduled_delivery:
            lines.append(f"{t.MSG_DELIVERY_SCHEDULED.format(date=_format_date(ts.scheduled_delivery))}")
        return lines

    shipping_status = row.get("shipping_status", "")
    if shipping_status:
        lines.append(f"{t.MSG_DELIVERY_STATUS.format(status=esc(t.status(shipping_status)))}")
    location_parts = [p for p in (row.get("delivery_city", ""), row.get("receive_point", "")) if p]
    if location_parts:
        lines.append(f"📍 {esc(', '.join(location_parts))}")
    return lines


def _format_delivery_block(row: dict, tracking_info: dict | None, t: Texts) -> str:
    """Format a single delivery block with tracking info."""
    ttn = row.get("tracking_code", "")
    lines = [_format_order_label(row, t), texts.with_logo(
        t.MSG_ORDER_TRACKING.format(code=texts.tracking_link(ttn)),
        texts.NOVA_POSHTA, "🚚")]
    return "\n".join(lines + parcel_lines(row, tracking_info, t))


def _menu_kb(t: Texts) -> InlineKeyboardMarkup:
    """The way back. Nothing else on this screen is pressable."""
    builder = InlineKeyboardBuilder()
    builder.button(text=t.BTN_MENU, callback_data=MenuAction(action="menu"))
    return builder.as_markup()


async def delivery_screen(
    chat_id: int,
    t: Texts,
    novaposhta: NovaPoshtaClient | None,
    anchor: Message,
) -> tuple[str, InlineKeyboardMarkup]:
    """The delivery screen: every parcel with a TTN, live from Nova Poshta.

    Reachable only from a keyboard sent before «🚚 Відслідкувати замовлення»
    left the menu — what it answers now lives on the order card. Kept working,
    and given the way back that every other screen has.
    """
    phone = await get_user_phone(chat_id)
    if not phone:
        # Same case, same words as the order screens: this is "we have no
        # number", not "that number found nothing".
        return t.MSG_NO_PHONE_YET, _menu_kb(t)

    tracked_orders = await get_orders_with_tracking(chat_id)
    track(chat_id, "delivery_viewed", found=len(tracked_orders))

    if not tracked_orders:
        # Distinguish "nothing shipped yet" from "nothing ordered yet".
        all_orders = await get_cached_orders(chat_id)
        return ((t.MSG_DELIVERY_NO_TRACKING if all_orders else t.MSG_NO_DELIVERIES),
                _menu_kb(t))

    # Nova Poshta can take a few seconds per parcel; show life instead of silence.
    await typing(anchor)

    tracking_map: dict = {}
    if novaposhta:
        ttns = [o["tracking_code"] for o in tracked_orders]
        tracking_map = await novaposhta.track_many(ttns, phone)

    blocks: list[str] = []
    max_len = 3800
    current_len = len(t.MSG_DELIVERY_HEADER) + 4

    for row in tracked_orders:
        ttn = row.get("tracking_code", "")
        block = _format_delivery_block(row, tracking_map.get(ttn), t)
        if current_len + len(block) + 4 > max_len:
            blocks.append("\n" + t.MSG_DELIVERIES_TRUNCATED)
            break
        blocks.append(block)
        current_len += len(block) + 4

    return t.MSG_DELIVERY_HEADER + "\n\n" + "\n\n".join(blocks), _menu_kb(t)
