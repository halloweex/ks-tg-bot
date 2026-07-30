"""Order display handler — show cached orders instantly, refresh in background."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime
from html import escape

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import (CallbackQuery, InlineKeyboardMarkup, KeyboardButton, Message,
                           ReplyKeyboardMarkup, ReplyKeyboardRemove)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from loguru import logger

from bot import texts
from bot.i18n import Texts, variants
from bot.callbacks import MenuAction, OrderAction
from bot.analytics import track
from bot.config import AppConfig
from bot.db import get_cached_orders, get_last_sync_time, get_user_phone, save_user, upsert_orders
from bot.keyboards import main_menu_kb
from bot.services.keycrm import KeyCRMClient, keycrm_order_to_dict
from bot.services.shopify import ShopifyClient, shopify_order_to_dict
from bot.tasks import spawn

router = Router()

# Skip a background refresh if the cache was synced more recently than this.
# Prevents a broadcast burst from firing thousands of redundant API fetches.
_REFRESH_TTL_SECONDS = 300

# Caps how many background refreshes hit the external APIs at once, so a burst
# of users after a promo push can't trigger KeyCRM/Shopify rate limits.
_refresh_semaphore = asyncio.Semaphore(10)

# 78% of orders have 4 items or fewer (measured over 43,374 orders), so at this
# threshold most orders show in full and only the long ones get a button.
_MAX_INLINE_ITEMS = 4
# What a shortened order still shows before "…and N more".
_COLLAPSED_ITEMS = 2


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def _money(value) -> str:
    """Format an amount without a pointless '.0' — CRM totals are whole hryvnia."""
    try:
        num = float(value)
    except (TypeError, ValueError):
        return escape(str(value))
    return str(int(num)) if num == int(num) else f"{num:.2f}"


def _order_products(row: dict) -> list[dict]:
    """Cached product lines for an order, or [] if the JSON is unusable."""
    try:
        return json.loads(row.get("products_json", "[]")) or []
    except (json.JSONDecodeError, TypeError):
        return []


def _item_line(product: dict, t: Texts) -> str:
    """One product as its own line, name shortened to stay readable on a phone.

    Sent with parse_mode="HTML", hence the escaping: product names really do
    contain '&'.
    """
    name = texts.shorten_name(product.get("name", ""))
    return f"   • {escape(name)} ×{escape(str(product.get('qty', '')))}"


def _format_cached_order(
    row: dict, t: Texts, *, is_latest: bool = False, expanded: bool = False
) -> str:
    """Format a single cached order (from DB dict) as a text block.

    Orders with more than _MAX_INLINE_ITEMS lines are shortened — 22% of orders
    have that many — and the caller offers a button to expand this one.
    """
    source_label = t.order_source_label(row)

    products = _order_products(row)
    if not products:
        item_lines = ["   -"]
    elif expanded or len(products) <= _MAX_INLINE_ITEMS:
        item_lines = [_item_line(p, t) for p in products]
    else:
        shown = products[:_COLLAPSED_ITEMS]
        item_lines = [_item_line(p, t) for p in shown]
        item_lines.append(
            "   " + t.MSG_ORDER_MORE_ITEMS.format(count=len(products) - len(shown))
        )

    ordered_at = row.get("ordered_at", "")
    try:
        dt = datetime.fromisoformat(ordered_at)
        date_str = dt.strftime("%d.%m.%Y")
    except (ValueError, TypeError):
        date_str = ordered_at or "-"

    status = escape(t.status(row.get("status_name", "")) or "-")
    total = _money(row.get("grand_total", 0))
    currency = escape(t.currency(row.get("currency", "грн")))

    prefix = f"{t.MSG_ORDER_LATEST}\n" if is_latest else ""

    lines = [
        f"{prefix}{escape(source_label)}",
        f"  {t.LBL_STATUS}: {status}",
        f"  {t.LBL_PRODUCTS}:",
        *item_lines,
        f"  {t.LBL_TOTAL}: {total} {currency}",
        f"  {t.LBL_DATE}: {escape(date_str)}",
    ]

    tracking = row.get("tracking_code", "")
    if tracking:
        lines.append(f"  {t.MSG_ORDER_TRACKING.format(code=texts.tracking_link(tracking))}")

    location_parts = [p for p in (row.get("delivery_city", ""), row.get("receive_point", "")) if p]
    if location_parts:
        location = escape(", ".join(location_parts))
        lines.append(f"  {t.MSG_ORDER_LOCATION.format(location=location)}")

    return "\n".join(lines)


def _format_orders_from_cache(
    orders: list[dict], t: Texts, expanded_id: int = 0
) -> str:
    """Format all cached orders into a single message text.

    `expanded_id` is the cache row whose full item list should be shown; every
    other long order stays shortened. Only one at a time, so the message can't
    grow past Telegram's limit and the state fits in the callback data.
    """
    if not orders:
        return t.MSG_NO_ORDERS

    max_len = 3800
    header = t.MSG_ORDERS_HEADER + "\n\n"
    result_parts: list[str] = []
    current_len = len(header)

    for i, row in enumerate(orders):
        block = _format_cached_order(
            row, t, is_latest=(i == 0), expanded=(row.get("id") == expanded_id)
        ) + "\n"
        if current_len + len(block) + 2 > max_len:
            result_parts.append("\n" + t.MSG_ORDERS_TRUNCATED)
            break
        result_parts.append(block)
        current_len += len(block) + 2

    return header + "\n".join(result_parts)


def _order_date(row: dict) -> str:
    """dd.mm.yyyy for an order, or '' if the stored timestamp is unparseable."""
    try:
        return datetime.fromisoformat(row.get("ordered_at", "")).strftime("%d.%m.%Y")
    except (ValueError, TypeError):
        return ""


def _button_label(row: dict, t: Texts) -> str:
    """Identify an order in a button: source label plus its date.

    The source label alone is not enough — most orders are Instagram ones with
    no order number, so several buttons in the same message would read
    identically and there would be no way to tell which is which.
    """
    label = t.order_source_label(row)
    date = _order_date(row)
    return f"{label}, {date}" if date else label


def _orders_kb(orders: list[dict], t: Texts, expanded_id: int = 0) -> InlineKeyboardMarkup:
    """Expand/collapse buttons for the orders that had to be shortened."""
    builder = InlineKeyboardBuilder()
    for row in orders:
        if len(_order_products(row)) <= _MAX_INLINE_ITEMS:
            continue
        row_id = row.get("id", 0)
        label = _button_label(row, t)
        if row_id == expanded_id:
            builder.button(
                text=t.BTN_HIDE_ITEMS.format(order=label),
                callback_data=OrderAction(action="items", order_id=0),
            )
        else:
            builder.button(
                text=t.BTN_SHOW_ITEMS.format(order=label),
                callback_data=OrderAction(action="items", order_id=row_id),
            )
    builder.button(text=t.BTN_MENU, callback_data=MenuAction(action="back"))
    builder.adjust(1)
    return builder.as_markup()


def _no_orders_kb(t: Texts) -> InlineKeyboardMarkup:
    """Offer support when the lookup found nothing.

    A customer who just shared their contact and got "no orders" has nowhere to
    go otherwise, and the most likely cause — the order sits under a different
    phone than their Telegram — is something only a manager can resolve.
    """
    builder = InlineKeyboardBuilder()
    builder.button(text=t.BTN_SUPPORT, callback_data=MenuAction(action="support"))
    builder.button(text=t.BTN_MENU, callback_data=MenuAction(action="back"))
    builder.adjust(1)
    return builder.as_markup()


def _menu_reply_kb(t: Texts) -> ReplyKeyboardMarkup:
    """Reply keyboard with a single Menu button."""
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=t.BTN_MENU)]],
        resize_keyboard=True,
    )


# ---------------------------------------------------------------------------
# Background refresh
# ---------------------------------------------------------------------------

async def _is_cache_fresh(chat_id: int) -> bool:
    """True if the cached orders were synced within the TTL window.

    synced_at is written as SQLite datetime('now') (UTC, 'YYYY-MM-DD HH:MM:SS').
    """
    last = await get_last_sync_time(chat_id)
    if not last:
        return False
    try:
        synced = datetime.strptime(last, "%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        return False
    return (datetime.utcnow() - synced).total_seconds() < _REFRESH_TTL_SECONDS


async def _refresh_orders(
    chat_id: int,
    phone: str,
    keycrm: KeyCRMClient,
    shopify: ShopifyClient | None,
) -> None:
    """Fetch fresh orders from APIs and upsert into cache.

    Bounded by _refresh_semaphore so concurrent refreshes can't overwhelm the
    external APIs during a post-broadcast activity burst.
    """
    async with _refresh_semaphore:
        await _do_refresh_orders(chat_id, phone, keycrm, shopify)


async def _do_refresh_orders(
    chat_id: int,
    phone: str,
    keycrm: KeyCRMClient,
    shopify: ShopifyClient | None,
) -> None:
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
    # External ids of orders KeyCRM already knows about. Anything Shopify
    # returns with a matching id is the same physical order, and would
    # otherwise be listed twice with two different statuses.
    keycrm_external_ids: set[str] = set()

    if not isinstance(keycrm_result, Exception):
        db_rows.extend(keycrm_order_to_dict(o, chat_id) for o in keycrm_result)
        keycrm_external_ids = {o.external_id for o in keycrm_result if o.external_id}
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
                except Exception:
                    pass

    if not isinstance(shopify_result, Exception):
        skipped = 0
        for order in shopify_result:
            row = shopify_order_to_dict(order, chat_id)
            if row["external_id"] and row["external_id"] in keycrm_external_ids:
                skipped += 1
                continue
            db_rows.append(row)
        if skipped:
            logger.debug(
                "Deduped {} Shopify order(s) already present in KeyCRM for chat {}",
                skipped, chat_id,
            )

    if db_rows:
        await upsert_orders(chat_id, db_rows)


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

async def _send_orders(message: Message, chat_id: int, t: Texts) -> None:
    """Read cached orders and send them with expand/collapse buttons."""
    cached = await get_cached_orders(chat_id)
    await message.answer(
        _format_orders_from_cache(cached, t),
        reply_markup=_orders_kb(cached, t) if cached else _no_orders_kb(t),
        parse_mode="HTML",
    )


@router.callback_query(MenuAction.filter(F.action == "orders"))
async def show_orders(
    callback: CallbackQuery,
    callback_data: MenuAction,
    keycrm: KeyCRMClient,
    shopify: ShopifyClient | None,
    config: AppConfig,
    t: Texts,
) -> None:
    """Display orders from local cache, refresh from APIs in background."""
    await callback.answer()

    chat_id = callback.from_user.id
    phone = await get_user_phone(chat_id)
    if not phone:
        await callback.message.answer(
            t.ERR_PHONE_NOT_FOUND,
            reply_markup=_menu_reply_kb(t),
        )
        return

    # Try to show cached orders instantly
    cached = await get_cached_orders(chat_id)

    if cached:
        track(chat_id, "orders_viewed", found=len(cached), cached=True)
        await callback.message.answer(
            _format_orders_from_cache(cached, t),
            reply_markup=_orders_kb(cached, t),
            parse_mode="HTML",
        )
        # Fire-and-forget background refresh — but only if the cache is stale,
        # so repeated taps and post-broadcast bursts don't re-hit the APIs.
        if not await _is_cache_fresh(chat_id):
            spawn(_refresh_orders(chat_id, phone, keycrm, shopify), name="refresh_orders")
    else:
        # No cache — show loading, fetch synchronously, then display
        await callback.message.answer(t.MSG_ORDERS_LOADING)
        await _refresh_orders(chat_id, phone, keycrm, shopify)
        # Tracked after the fetch, not before: an empty cache says nothing about
        # whether the phone matched, and `found=0` here is exactly the signal
        # that a customer shared their contact and saw nothing.
        fetched = await get_cached_orders(chat_id)
        track(chat_id, "orders_viewed", found=len(fetched), cached=False)
        await _send_orders(callback.message, chat_id, t)


@router.callback_query(OrderAction.filter(F.action == "items"))
async def toggle_order_items(
    callback: CallbackQuery,
    callback_data: OrderAction,
    t: Texts,
) -> None:
    """Expand or collapse one order's item list, in place.

    The list is re-read for the caller's own chat, so the id in the callback can
    only ever pick one of their orders — a forged id simply expands nothing.
    """
    await callback.answer()
    track(callback.from_user.id, "order_items_toggled",
          expanded=bool(callback_data.order_id))

    cached = await get_cached_orders(callback.from_user.id)
    if not cached:
        return

    expanded_id = callback_data.order_id
    try:
        await callback.message.edit_text(
            _format_orders_from_cache(cached, t, expanded_id),
            reply_markup=_orders_kb(cached, t, expanded_id),
            parse_mode="HTML",
        )
    except TelegramBadRequest:
        # Same content (double tap) or the message is too old to edit.
        logger.debug("Order list edit skipped for chat {}", callback.from_user.id)


@router.message(F.text.in_(variants("BTN_MENU")))
async def menu_button_handler(
    message: Message,
    config: AppConfig,
    t: Texts,
) -> None:
    """Handle the reply-keyboard Menu button — show main menu."""
    await message.answer(
        t.MSG_MAIN_MENU,
        reply_markup=main_menu_kb(t, config.website_url),
    )
