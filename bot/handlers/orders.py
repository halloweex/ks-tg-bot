"""Order display handler — fetch, merge, format, and show orders from both APIs."""
from __future__ import annotations

import asyncio
from datetime import datetime

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
from loguru import logger

from bot import texts
from bot.callbacks import MenuAction
from bot.config import AppConfig
from bot.db import get_user_phone
from bot.services.keycrm import KeyCRMClient, KeyCRMOrder
from bot.services.shopify import ShopifyClient, ShopifyOrder

router = Router()


def _format_keycrm_order(order: KeyCRMOrder) -> str:
    """Format a single KeyCRM order as a text block with Instagram source label."""
    products_str = ", ".join(
        f"{p['name']} x {p['qty']}" for p in order.products
    ) if order.products else "-"

    try:
        dt = datetime.fromisoformat(order.ordered_at)
        date_str = dt.strftime("%d.%m.%Y")
    except (ValueError, TypeError):
        date_str = order.ordered_at or "-"

    return (
        f"{texts.MSG_ORDER_SOURCE_INSTAGRAM}\n"
        f"  Статус: {order.status_name}\n"
        f"  Товари: {products_str}\n"
        f"  Сума: {order.grand_total} грн\n"
        f"  Дата: {date_str}"
    )


def _format_shopify_order(order: ShopifyOrder) -> str:
    """Format a single Shopify order as a text block with web source label."""
    products_str = ", ".join(
        f"{p['name']} x {p['qty']}" for p in order.line_items
    ) if order.line_items else "-"

    try:
        dt = datetime.fromisoformat(order.created_at)
        date_str = dt.strftime("%d.%m.%Y")
    except (ValueError, TypeError):
        date_str = order.created_at or "-"

    status = order.fulfillment_status or order.financial_status or "-"

    return (
        f"{texts.MSG_ORDER_SOURCE_WEB} {order.name}\n"
        f"  Статус: {status}\n"
        f"  Товари: {products_str}\n"
        f"  Сума: {order.total_price} {order.currency}\n"
        f"  Дата: {date_str}"
    )


def _parse_date_key(date_str: str) -> str:
    """Parse a date string to ISO format for sorting. Return raw string on failure."""
    try:
        return datetime.fromisoformat(date_str).isoformat()
    except (ValueError, TypeError):
        return ""


def _format_all_orders(
    keycrm_result: list[KeyCRMOrder] | Exception,
    shopify_result: list[ShopifyOrder] | Exception,
) -> str:
    """Merge and format orders from both APIs into a single text.

    Args:
        keycrm_result: List of KeyCRM orders or an Exception if the call failed.
        shopify_result: List of Shopify orders or an Exception if the call failed.

    Returns:
        Formatted text ready for Telegram message.
    """
    # Check if both APIs failed
    both_failed = isinstance(keycrm_result, Exception) and isinstance(shopify_result, Exception)
    if both_failed:
        return texts.ERR_API_UNAVAILABLE

    # Build (sort_key, formatted_text) tuples
    items: list[tuple[str, str]] = []

    if not isinstance(keycrm_result, Exception):
        for order in keycrm_result:
            sort_key = _parse_date_key(order.ordered_at)
            items.append((sort_key, _format_keycrm_order(order)))

    if not isinstance(shopify_result, Exception):
        for order in shopify_result:
            sort_key = _parse_date_key(order.created_at)
            items.append((sort_key, _format_shopify_order(order)))

    if not items:
        return texts.MSG_NO_ORDERS

    # Sort by date descending (newest first)
    items.sort(key=lambda x: x[0], reverse=True)

    # Build text respecting 4096 char Telegram limit (leave margin for header)
    max_len = 3800
    header = texts.MSG_ORDERS_HEADER + "\n\n"
    result_parts: list[str] = []
    current_len = len(header)

    for _, formatted in items:
        block = formatted + "\n"
        if current_len + len(block) + 2 > max_len:
            result_parts.append("\n...та інші замовлення")
            break
        result_parts.append(block)
        current_len += len(block) + 2  # account for separator

    return header + "\n".join(result_parts)


def _back_to_menu_kb() -> "InlineKeyboardBuilder":
    """Build a single-button keyboard to return to main menu."""
    builder = InlineKeyboardBuilder()
    builder.button(text=texts.BTN_BACK, callback_data=MenuAction(action="back"))
    builder.adjust(1)
    return builder.as_markup()


@router.callback_query(MenuAction.filter(F.action == "orders"))
async def show_orders(
    callback: CallbackQuery,
    callback_data: MenuAction,
    keycrm: KeyCRMClient,
    shopify: ShopifyClient | None,
    config: AppConfig,
) -> None:
    """Display merged order list from Shopify and KeyCRM."""
    await callback.answer()

    # Get user phone from DB
    phone = await get_user_phone(callback.from_user.id)
    if not phone:
        try:
            await callback.message.edit_text(
                texts.ERR_PHONE_NOT_FOUND,
                reply_markup=_back_to_menu_kb(),
            )
        except TelegramBadRequest:
            await callback.message.answer(
                texts.ERR_PHONE_NOT_FOUND,
                reply_markup=_back_to_menu_kb(),
            )
        return

    # Show loading indicator
    try:
        await callback.message.edit_text(texts.MSG_ORDERS_LOADING)
    except TelegramBadRequest:
        pass  # message unchanged is fine, continue

    # Build coroutines for parallel fetch
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

    # Format merged results
    formatted_text = _format_all_orders(keycrm_result, shopify_result)

    # Display orders with back button (parse_mode=None to avoid HTML issues)
    back_kb = _back_to_menu_kb()
    try:
        await callback.message.edit_text(
            formatted_text,
            reply_markup=back_kb,
            parse_mode=None,
        )
    except TelegramBadRequest as exc:
        logger.debug("edit_text failed ({}), sending new message", exc.message)
        await callback.message.answer(
            formatted_text,
            reply_markup=back_kb,
            parse_mode=None,
        )
