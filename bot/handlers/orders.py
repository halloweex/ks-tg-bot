"""Order display handler — show cached orders instantly, refresh in background."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime
from html import escape

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from loguru import logger

from bot import texts
from bot.i18n import Texts, operator_texts, variants
from bot.callbacks import DiscountAction, MenuAction, OrderAction, StockAction
from bot.analytics import track
from bot.config import AppConfig
from bot.db import (CANCELLED_STATUS_GROUP, add_discount_request, add_stock_subscription,
                    get_cached_orders,
                    get_last_sync_time, get_stock_levels, get_subscribed_skus,
                    get_user_phone, recent_discount_request,
                    remove_stock_subscription, save_user, upsert_orders)
from bot.keyboards import main_menu_kb, menu_only_kb
from bot.screen import render, typing
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

# Orders per page. 92% of customers have five or fewer in total, so for almost
# everyone this is their whole history on one screen with no buttons at all;
# the long tail gets paging instead of a truncation notice it cannot act on.
_ORDERS_PER_PAGE = 5


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
    row: dict, t: Texts, *, number: int, is_latest: bool = False, expanded: bool = False
) -> str:
    """Format a single cached order (from DB dict) as a text block.

    `number` is the order's position in the whole list, counted across pages. It
    is what the expand button carries, so the two can be matched by eye.

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

    mark = t.MSG_ORDER_LATEST_MARK if is_latest else ""

    lines = [
        f"{mark}<b>{number}. {escape(source_label)}</b>",
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


def _page_slice(orders: list[dict], page: int) -> tuple[list[dict], int]:
    """The orders on `page`, plus the clamped page number."""
    if not orders:
        return [], 0
    last_page = max(0, (len(orders) - 1) // _ORDERS_PER_PAGE)
    page = min(max(page, 0), last_page)
    start = page * _ORDERS_PER_PAGE
    return orders[start:start + _ORDERS_PER_PAGE], page


def _format_orders_from_cache(
    orders: list[dict], t: Texts, expanded_id: int = 0, page: int = 0
) -> str:
    """Format one page of cached orders into a single message text.

    `expanded_id` is the cache row whose full item list should be shown; every
    other long order stays shortened. Only one at a time, so the message cannot
    grow past Telegram's limit and the state fits in the callback data.
    """
    if not orders:
        return t.MSG_NO_ORDERS

    visible, page = _page_slice(orders, page)
    start = page * _ORDERS_PER_PAGE

    header = t.MSG_ORDERS_HEADER
    if len(orders) > _ORDERS_PER_PAGE:
        header += "\n" + t.MSG_ORDERS_PAGE.format(
            first=start + 1, last=start + len(visible), total=len(orders)
        )
    # A row of "🔎 3" buttons is unreadable without saying once what the number
    # refers to; the line only appears when such a button exists.
    if any(len(_order_products(row)) > _MAX_INLINE_ITEMS for row in visible):
        header += "\n" + t.MSG_ORDERS_EXPAND_HINT
    header += "\n\n"

    # A page of five collapsed orders is far inside Telegram's 4096, but an
    # expanded order with many items could still push it; keep the guard.
    max_len = 3800
    result_parts: list[str] = []
    current_len = len(header)

    for i, row in enumerate(visible):
        block = _format_cached_order(
            row, t,
            number=start + i + 1,
            is_latest=(start + i == 0),
            expanded=(row.get("id") == expanded_id),
        ) + "\n"
        if current_len + len(block) + 2 > max_len:
            result_parts.append("\n" + t.MSG_ORDERS_TRUNCATED)
            break
        result_parts.append(block)
        current_len += len(block) + 2

    return header + "\n".join(result_parts)


def _short_date(raw: str) -> str:
    """dd.mm.yyyy from a stored timestamp, or the raw value if unparseable."""
    try:
        return datetime.fromisoformat(raw).strftime("%d.%m.%Y")
    except (ValueError, TypeError):
        return raw or ""


def _orders_kb(
    orders: list[dict], t: Texts, expanded_id: int = 0, page: int = 0
) -> InlineKeyboardMarkup:
    """Paging, plus expand/collapse for the shortened orders on this page.

    Expand buttons are labelled with the order's number in the list, so they fit
    several to a row. They used to carry the source and the date — "🔎 Товари:
    📸 Instagram, 15.06.2026" — which is one button per row and still ambiguous
    when two Instagram orders share a day.
    """
    builder = InlineKeyboardBuilder()
    visible, page = _page_slice(orders, page)
    start = page * _ORDERS_PER_PAGE

    expand_buttons = 0
    for i, row in enumerate(visible):
        if len(_order_products(row)) <= _MAX_INLINE_ITEMS:
            continue
        row_id = row.get("id", 0)
        number = start + i + 1
        if row_id == expanded_id:
            builder.button(
                text=t.BTN_HIDE_ITEMS.format(order=number),
                callback_data=OrderAction(action="items", order_id=0, page=page),
            )
        else:
            builder.button(
                text=t.BTN_SHOW_ITEMS.format(order=number),
                callback_data=OrderAction(action="items", order_id=row_id, page=page),
            )
        expand_buttons += 1

    # Paging keeps the expanded order id: moving pages collapses nothing, and
    # the id simply does not match anything on the new page.
    nav: list[tuple[str, int]] = []
    if page > 0:
        nav.append((t.BTN_ORDERS_NEWER, page - 1))
    if (page + 1) * _ORDERS_PER_PAGE < len(orders):
        nav.append((t.BTN_ORDERS_OLDER, page + 1))
    for text, target in nav:
        builder.button(
            text=text,
            callback_data=OrderAction(action="items", order_id=expanded_id, page=target),
        )

    builder.button(text=t.BTN_MENU, callback_data=MenuAction(action="back"))

    # All expand buttons on one row — there are at most _ORDERS_PER_PAGE of
    # them and each is a glyph and a number — then paging, then Menu.
    layout = [expand_buttons] if expand_buttons else []
    if nav:
        layout.append(len(nav))
    layout.append(1)
    builder.adjust(*layout)
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
    builder.adjust(2)
    return builder.as_markup()


def favourite_products(orders: list[dict], limit: int = 5) -> list[dict]:
    """The customer's most-ordered products, best first.

    Ranked by how many separate orders contain the product, then by total
    quantity, then by recency. Frequency alone ties heavily: a quarter of
    customers have only ever bought one product, and among those with several,
    most items were bought exactly once — the tiebreaks keep the list stable and
    meaningful instead of arbitrary.

    Grouped by sku where the cache has one (older rows predate it) so a product
    renamed in the CRM does not split into two entries.
    """
    agg: dict[str, dict] = {}
    for row in orders:
        # An order that never happened says nothing about what they like.
        if row.get("status_group_id") == CANCELLED_STATUS_GROUP:
            continue
        ordered_at = row.get("ordered_at", "")
        for product in _order_products(row):
            name = str(product.get("name", "")).strip()
            if not name:
                continue
            key = str(product.get("sku") or "").strip() or name.lower()
            entry = agg.setdefault(
                key,
                # sku carried through so the stock lookup and the back-in-stock
                # subscription have something to key on.
                {"name": name, "sku": str(product.get("sku") or ""),
                 "orders": 0, "qty": 0, "last": ""},
            )
            entry["orders"] += 1
            try:
                entry["qty"] += int(product.get("qty") or 0)
            except (TypeError, ValueError):
                pass
            if ordered_at > entry["last"]:
                entry["last"] = ordered_at
                entry["name"] = name          # prefer the most recent spelling

    ranked = sorted(
        agg.values(), key=lambda e: (e["orders"], e["qty"], e["last"]), reverse=True
    )
    return ranked[:limit]


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

async def _render_orders(callback: CallbackQuery, chat_id: int, t: Texts) -> None:
    """Draw the cached orders, with expand/collapse buttons, on the screen."""
    cached = await get_cached_orders(chat_id)
    await render(
        callback,
        _format_orders_from_cache(cached, t),
        _orders_kb(cached, t) if cached else _no_orders_kb(t),
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
        await render(callback, t.ERR_PHONE_NOT_FOUND, menu_only_kb(t))
        return

    # Try to show cached orders instantly
    cached = await get_cached_orders(chat_id)

    if cached:
        track(chat_id, "orders_viewed", found=len(cached), cached=True)
        await render(
            callback,
            _format_orders_from_cache(cached, t),
            _orders_kb(cached, t),
        )
        # Fire-and-forget background refresh — but only if the cache is stale,
        # so repeated taps and post-broadcast bursts don't re-hit the APIs.
        if not await _is_cache_fresh(chat_id):
            spawn(_refresh_orders(chat_id, phone, keycrm, shopify), name="refresh_orders")
    else:
        # No cache — show loading, fetch synchronously, then display
        await typing(callback.message)
        await _refresh_orders(chat_id, phone, keycrm, shopify)
        # Tracked after the fetch, not before: an empty cache says nothing about
        # whether the phone matched, and `found=0` here is exactly the signal
        # that a customer shared their contact and saw nothing.
        fetched = await get_cached_orders(chat_id)
        track(chat_id, "orders_viewed", found=len(fetched), cached=False)
        await _render_orders(callback, chat_id, t)


@router.callback_query(MenuAction.filter(F.action == "favourites"))
async def show_favourites(
    callback: CallbackQuery,
    callback_data: MenuAction,
    keycrm: KeyCRMClient,
    shopify: ShopifyClient | None,
    t: Texts,
) -> None:
    """Show the products this customer orders most often.

    Computed from the cached orders, which already carry their product lines —
    no extra API call. Falls back to a fetch when the cache is cold so the very
    first visit is not empty.
    """
    chat_id = callback.from_user.id
    await callback.answer()

    phone = await get_user_phone(chat_id)
    if not phone:
        await render(callback, t.ERR_PHONE_NOT_FOUND, menu_only_kb(t))
        return

    cached = await get_cached_orders(chat_id)
    if not cached:
        await typing(callback.message)
        await _refresh_orders(chat_id, phone, keycrm, shopify)
        cached = await get_cached_orders(chat_id)

    text, markup, found = await _favourites_view(chat_id, t, cached)
    track(chat_id, "favourites_viewed", found=found)
    await render(callback, text, markup)


async def _favourites_view(
    chat_id: int, t: Texts, cached: list[dict]
) -> tuple[str, InlineKeyboardMarkup, int]:
    """The favourites screen — its text, its buttons, and how many it lists.

    Built in one place because two handlers draw it: opening the screen, and
    toggling a back-in-stock subscription, which has to redraw so the button
    the customer just pressed changes to reflect what it did.
    """
    favourites = favourite_products(cached)
    if not favourites:
        return (t.MSG_NO_FAVOURITES if cached else t.MSG_NO_ORDERS), _no_orders_kb(t), 0

    levels = await get_stock_levels()
    subscribed = await get_subscribed_skus(chat_id)

    repeated = any(item["orders"] > 1 for item in favourites)
    header = t.MSG_FAVOURITES_HEADER if repeated else t.MSG_FAVOURITES_HEADER_ONCE
    lines = [header, ""]
    any_out_of_stock = False
    for i, item in enumerate(favourites, 1):
        line = "   " + t.MSG_FAVOURITE_LINE.format(
            orders=item["orders"], qty=item["qty"],
            date=escape(_short_date(item["last"])),
        )
        if _is_out_of_stock(item, levels):
            line += f" · {escape(t.MSG_FAVOURITE_OUT_OF_STOCK)}"
            any_out_of_stock = True
        lines.append(f"<b>{i}.</b> {escape(texts.shorten_name(item['name'], 52))}")
        lines.append(line)
    # Same reason as the order list: the buttons below are numbers, and the
    # numbers only mean something once they have been explained.
    if any_out_of_stock:
        lines += ["", t.MSG_STOCK_HINT]

    return (
        "\n".join(lines),
        _favourites_kb(favourites, levels, subscribed, t),
        len(favourites),
    )


def _is_out_of_stock(item: dict, levels: dict[str, int]) -> bool:
    """True only when stock is known and says there is none free to sell.

    An unknown sku is never reported as missing: rows cached before skus were
    stored have none, and claiming "out of stock" for something we simply cannot
    look up would be worse than staying quiet.
    """
    sku = str(item.get("sku") or "")
    return bool(sku) and sku in levels and levels[sku] <= 0


def _favourites_kb(favourites, levels, subscribed, t: Texts) -> InlineKeyboardMarkup:
    """A notify-me button for each favourite that is currently unavailable.

    Labelled with the product's number in the list above, so up to five of them
    fit on one row instead of five rows of truncated product names.
    """
    builder = InlineKeyboardBuilder()
    notify_buttons = 0
    for i, item in enumerate(favourites, 1):
        sku = str(item.get("sku") or "")
        if not _is_out_of_stock(item, levels):
            continue
        if sku in subscribed:
            builder.button(text=t.BTN_NOTIFY_CANCEL.format(product=i),
                           callback_data=StockAction(action="unsub", sku=sku))
        else:
            builder.button(text=t.BTN_NOTIFY_ME.format(product=i),
                           callback_data=StockAction(action="sub", sku=sku))
        notify_buttons += 1
    builder.button(text=t.BTN_WANT_DISCOUNT,
                   callback_data=DiscountAction(action="ask"))
    builder.button(text=t.BTN_MENU, callback_data=MenuAction(action="back"))
    layout = [notify_buttons] if notify_buttons else []
    layout += [1, 1]
    builder.adjust(*layout)
    return builder.as_markup()


@router.callback_query(DiscountAction.filter(F.action == "ask"))
async def request_discount(
    callback: CallbackQuery,
    callback_data: DiscountAction,
    config: AppConfig,
    t: Texts,
) -> None:
    """Pass a discount request, with the customer's favourites, to a manager.

    Deliberately not an automatically issued code: there is no discount policy
    yet, and the bot inventing one would commit the business to it. The manager
    answers through the existing support relay, which is why the chat_id line is
    formatted the same way — replying to it routes back to this customer.
    """
    chat_id = callback.from_user.id

    # Answered as a pop-up rather than a message: the confirmation belongs to
    # the tap, and the favourites list the customer is looking at should stay
    # where it is.
    if await recent_discount_request(chat_id):
        await callback.answer(t.MSG_DISCOUNT_ALREADY, show_alert=True)
        return

    favourites = favourite_products(await get_cached_orders(chat_id))
    if not favourites:
        await callback.answer()
        return

    await add_discount_request(chat_id, json.dumps(
        [{"sku": f["sku"], "name": f["name"], "orders": f["orders"]} for f in favourites],
        ensure_ascii=False,
    ))
    track(chat_id, "discount_requested", products=len(favourites))

    op = operator_texts()
    lines = [op.MSG_DISCOUNT_ADMIN.format(chat_id=chat_id), ""]
    lines += [
        f"• {escape(texts.shorten_name(f['name'], 60))} — "
        f"{op.MSG_FAVOURITE_LINE.format(orders=f['orders'], qty=f['qty'], date=_short_date(f['last']))}"
        for f in favourites
    ]
    lines += ["", escape(op.MSG_SUPPORT_REPLY_INSTRUCTION)]
    try:
        await callback.bot.send_message(
            config.support_chat_id, "\n".join(lines), parse_mode="HTML"
        )
    except Exception as exc:  # noqa: BLE001 — the customer must still get an answer
        logger.warning("Discount request not delivered to support: {}", exc)

    await callback.answer(t.MSG_DISCOUNT_SENT, show_alert=True)


@router.callback_query(StockAction.filter())
async def toggle_stock_subscription(
    callback: CallbackQuery,
    callback_data: StockAction,
    t: Texts,
) -> None:
    """Subscribe to, or unsubscribe from, a product coming back in stock."""
    chat_id = callback.from_user.id
    sku = callback_data.sku

    if callback_data.action == "unsub":
        await remove_stock_subscription(chat_id, sku)
        track(chat_id, "stock_unsubscribed")
        await callback.answer(t.MSG_UNSUBSCRIBED, show_alert=True)
        await _redraw_favourites(callback, chat_id, t)
        return

    # The product name comes from this customer's own cached orders, so a
    # forged sku subscribes to nothing rather than to somebody else's product.
    name = ""
    for row in await get_cached_orders(chat_id):
        for product in _order_products(row):
            if str(product.get("sku") or "") == sku:
                name = str(product.get("name", ""))
                break
        if name:
            break
    if not name:
        await callback.answer()
        return

    await add_stock_subscription(chat_id, sku, name)
    track(chat_id, "stock_subscribed")
    await callback.answer(t.MSG_SUBSCRIBED, show_alert=True)
    await _redraw_favourites(callback, chat_id, t)


async def _redraw_favourites(callback: CallbackQuery, chat_id: int, t: Texts) -> None:
    """Redraw the favourites screen after a subscription changed.

    Without this the button keeps offering what the customer just did: they tap
    "🔔 2", the pop-up says we will write when it is back, and the button still
    reads 🔔. These buttons exist nowhere else, so the screen the callback came
    from is always the favourites list.
    """
    text, markup, _found = await _favourites_view(
        chat_id, t, await get_cached_orders(chat_id)
    )
    await render(callback, text, markup)


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
    page = callback_data.page
    await render(
        callback,
        _format_orders_from_cache(cached, t, expanded_id, page),
        _orders_kb(cached, t, expanded_id, page),
    )


@router.message(F.text.in_(variants("BTN_MENU")))
async def menu_button_handler(
    message: Message,
    config: AppConfig,
    t: Texts,
) -> None:
    """The «📋 Меню» button under the input field — open the main menu.

    Matched by text in every language the button can be rendered in, because a
    reply keyboard sends its own label as an ordinary message. The menu arrives
    as a new message rather than an edit: the tap is a message of the
    customer's own, so there is nothing above it left to edit into.
    """
    await message.answer(
        t.MSG_MAIN_MENU,
        reply_markup=main_menu_kb(t, config.website_url),
    )
