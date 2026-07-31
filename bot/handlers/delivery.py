"""Delivery status handler — show Nova Poshta tracking for user's orders."""
from __future__ import annotations

import json
from datetime import datetime
from html import escape

from aiogram import F, Router
from aiogram.types import CallbackQuery

from bot import texts
from bot.i18n import Texts
from bot.callbacks import DeliveryAction
from bot.analytics import track
from bot.config import AppConfig
from bot.db import get_orders_with_tracking, get_user_phone, get_cached_orders
from bot.keyboards import menu_only_kb
from bot.screen import render
from bot.services.novaposhta import NovaPoshtaClient

router = Router()


def _format_order_label(row: dict, t: Texts) -> str:
    """Short label for an order: source + products summary."""
    label = escape(t.order_source_label(row))

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


def _format_delivery_block(row: dict, tracking_info: dict | None, t: Texts) -> str:
    """Format a single delivery block with tracking info."""
    label = _format_order_label(row, t)
    ttn = row.get("tracking_code", "")

    lines = [label, f"  {t.MSG_ORDER_TRACKING.format(code=texts.tracking_link(ttn))}"]

    if tracking_info:
        ts = tracking_info
        if ts.status:
            lines.append(f"  {t.MSG_DELIVERY_STATUS.format(status=escape(t.status(ts.status)))}")
        if ts.warehouse_recipient:
            lines.append(f"  {t.MSG_DELIVERY_WAREHOUSE.format(warehouse=escape(ts.warehouse_recipient))}")
        if ts.actual_delivery:
            lines.append(f"  {t.MSG_DELIVERY_ACTUAL.format(date=_format_date(ts.actual_delivery))}")
        elif ts.scheduled_delivery:
            lines.append(f"  {t.MSG_DELIVERY_SCHEDULED.format(date=_format_date(ts.scheduled_delivery))}")
    else:
        # Fallback: use data from CRM
        shipping_status = row.get("shipping_status", "")
        if shipping_status:
            lines.append(f"  {t.MSG_DELIVERY_STATUS.format(status=escape(t.status(shipping_status)))}")
        location_parts = [p for p in (row.get("delivery_city", ""), row.get("receive_point", "")) if p]
        if location_parts:
            lines.append(f"  📍 {escape(', '.join(location_parts))}")

    return "\n".join(lines)


@router.callback_query(DeliveryAction.filter(F.action == "view"))
async def show_delivery_status(
    callback: CallbackQuery,
    callback_data: DeliveryAction,
    config: AppConfig,
    novaposhta: NovaPoshtaClient | None,
    t: Texts,
) -> None:
    """Show delivery tracking status for orders with TTNs."""
    await callback.answer()

    chat_id = callback.from_user.id
    phone = await get_user_phone(chat_id)
    if not phone:
        await render(callback, t.ERR_PHONE_NOT_FOUND, menu_only_kb(t))
        return

    tracked_orders = await get_orders_with_tracking(chat_id)
    track(chat_id, "delivery_viewed", found=len(tracked_orders))

    if not tracked_orders:
        # Check if user has orders at all but none with tracking
        all_orders = await get_cached_orders(chat_id)
        if all_orders:
            msg = t.MSG_DELIVERY_NO_TRACKING
        else:
            msg = t.MSG_NO_DELIVERIES
        await render(callback, msg, menu_only_kb(t))
        return

    # Nova Poshta can take a few seconds per parcel; show life instead of silence.
    try:
        await callback.bot.send_chat_action(callback.message.chat.id, "typing")
    except Exception:  # noqa: BLE001
        pass

    # Fetch real-time status from Nova Poshta if available
    tracking_map: dict = {}
    if novaposhta:
        ttns = [o["tracking_code"] for o in tracked_orders]
        tracking_map = await novaposhta.track_many(ttns, phone)

    # Format output
    blocks: list[str] = []
    max_len = 3800
    current_len = len(t.MSG_DELIVERY_HEADER) + 4

    for row in tracked_orders:
        ttn = row.get("tracking_code", "")
        info = tracking_map.get(ttn)
        block = _format_delivery_block(row, info, t)
        if current_len + len(block) + 4 > max_len:
            blocks.append("\n" + t.MSG_DELIVERIES_TRUNCATED)
            break
        blocks.append(block)
        current_len += len(block) + 4

    result = t.MSG_DELIVERY_HEADER + "\n\n" + "\n\n".join(blocks)

    await render(callback, result, menu_only_kb(t))
