"""Order display handler — show cached orders instantly, refresh in background."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime

from aiogram import F, Router
from aiogram.types import CallbackQuery, KeyboardButton, Message, ReplyKeyboardMarkup, ReplyKeyboardRemove
from loguru import logger

from bot import texts
from bot.callbacks import MenuAction
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


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def _format_cached_order(row: dict, *, is_latest: bool = False) -> str:
    """Format a single cached order (from DB dict) as a text block."""
    # A store order number means the order came from the website — whether the
    # row was fetched from Shopify directly or from KeyCRM, which mirrors the
    # number for orders its Shopify integration pulled in.
    order_name = row.get("order_name", "")

    if order_name:
        source_label = f"{texts.MSG_ORDER_SOURCE_WEB} {order_name}".strip()
    else:
        source_label = texts.MSG_ORDER_SOURCE_INSTAGRAM

    try:
        products = json.loads(row.get("products_json", "[]"))
    except (json.JSONDecodeError, TypeError):
        products = []

    products_str = ", ".join(
        f"{p['name']} x {p['qty']}" for p in products
    ) if products else "-"

    ordered_at = row.get("ordered_at", "")
    try:
        dt = datetime.fromisoformat(ordered_at)
        date_str = dt.strftime("%d.%m.%Y")
    except (ValueError, TypeError):
        date_str = ordered_at or "-"

    status = row.get("status_name", "") or "-"
    total = row.get("grand_total", 0)
    currency = row.get("currency", "грн")

    prefix = f"{texts.MSG_ORDER_LATEST}\n" if is_latest else ""

    lines = [
        f"{prefix}{source_label}",
        f"  Статус: {status}",
        f"  Товари: {products_str}",
        f"  Сума: {total} {currency}",
        f"  Дата: {date_str}",
    ]

    tracking = row.get("tracking_code", "")
    if tracking:
        lines.append(f"  {texts.MSG_ORDER_TRACKING.format(code=tracking)}")

    location_parts = [p for p in (row.get("delivery_city", ""), row.get("receive_point", "")) if p]
    if location_parts:
        lines.append(f"  {texts.MSG_ORDER_LOCATION.format(location=', '.join(location_parts))}")

    return "\n".join(lines)


def _format_orders_from_cache(orders: list[dict]) -> str:
    """Format all cached orders into a single message text."""
    if not orders:
        return texts.MSG_NO_ORDERS

    max_len = 3800
    header = texts.MSG_ORDERS_HEADER + "\n\n"
    result_parts: list[str] = []
    current_len = len(header)

    for i, row in enumerate(orders):
        block = _format_cached_order(row, is_latest=(i == 0)) + "\n"
        if current_len + len(block) + 2 > max_len:
            result_parts.append("\n...та інші замовлення")
            break
        result_parts.append(block)
        current_len += len(block) + 2

    return header + "\n".join(result_parts)


def _menu_reply_kb() -> ReplyKeyboardMarkup:
    """Reply keyboard with a single Menu button."""
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=texts.BTN_MENU)]],
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

async def _send_orders(message: Message, chat_id: int) -> None:
    """Read cached orders and send as a message with menu reply keyboard."""
    cached = await get_cached_orders(chat_id)
    formatted_text = _format_orders_from_cache(cached)
    await message.answer(
        formatted_text,
        reply_markup=_menu_reply_kb(),
        parse_mode=None,
    )


@router.callback_query(MenuAction.filter(F.action == "orders"))
async def show_orders(
    callback: CallbackQuery,
    callback_data: MenuAction,
    keycrm: KeyCRMClient,
    shopify: ShopifyClient | None,
    config: AppConfig,
) -> None:
    """Display orders from local cache, refresh from APIs in background."""
    await callback.answer()

    chat_id = callback.from_user.id
    phone = await get_user_phone(chat_id)
    if not phone:
        await callback.message.answer(
            texts.ERR_PHONE_NOT_FOUND,
            reply_markup=_menu_reply_kb(),
        )
        return

    # Try to show cached orders instantly
    cached = await get_cached_orders(chat_id)

    if cached:
        formatted_text = _format_orders_from_cache(cached)
        await callback.message.answer(
            formatted_text,
            reply_markup=_menu_reply_kb(),
            parse_mode=None,
        )
        # Fire-and-forget background refresh — but only if the cache is stale,
        # so repeated taps and post-broadcast bursts don't re-hit the APIs.
        if not await _is_cache_fresh(chat_id):
            spawn(_refresh_orders(chat_id, phone, keycrm, shopify), name="refresh_orders")
    else:
        # No cache — show loading, fetch synchronously, then display
        await callback.message.answer(texts.MSG_ORDERS_LOADING)
        await _refresh_orders(chat_id, phone, keycrm, shopify)
        await _send_orders(callback.message, chat_id)


@router.message(F.text == texts.BTN_MENU)
async def menu_button_handler(
    message: Message,
    config: AppConfig,
) -> None:
    """Handle the reply-keyboard Menu button — show main menu."""
    await message.answer(
        texts.MSG_MAIN_MENU,
        reply_markup=main_menu_kb(config.website_url),
    )
