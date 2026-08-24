"""Order display handler — show cached orders instantly, refresh in background."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime
from html import escape

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from loguru import logger

from core import texts
from core.i18n import Texts, operator_texts
from bot.callbacks import DiscountAction, MenuAction, OrderAction, StockAction
from bot.alerts import tell_admins_once
from bot.analytics import track
from core.config import AppConfig
from core.domain.offer import Offer
from core.repos.support import (add_discount_request, recent_discount_request,
                                remember_support_thread)
from core.repos.stock import (add_stock_subscription, get_stock_levels,
                              get_subscribed_skus, remove_stock_subscription)
from core.repos.catalogue import get_offers
from core.repos.orders import (CANCELLED_STATUS_GROUP, get_cached_orders,
                               get_last_sync_time)
from core.repos.users import get_user_phone
from bot.keyboards import cart_url
from bot.screen import render, typing
from bot.sync import stale_notice
from core.adapters.keycrm.client import KeyCRMClient
from core.usecases.sync_orders import sync_orders
from bot.tasks import spawn

router = Router()

# Skip a background refresh if the cache was synced more recently than this.
# Prevents a broadcast burst from firing thousands of redundant API fetches.
_REFRESH_TTL_SECONDS = 300

# Caps how many background refreshes hit the CRM at once, so a burst of users
# after a promo push can't trigger its rate limit.
_refresh_semaphore = asyncio.Semaphore(10)

# 78% of orders have 4 items or fewer (measured over 43,374 orders), so at this
# threshold most orders show in full and only the long ones get a button.
_MAX_INLINE_ITEMS = 4
# What a shortened order still shows before "…and N more".
_COLLAPSED_ITEMS = 2

# How much of a product name fits on a button next to its price. Telegram wraps
# a longer label onto a second line rather than cutting it, which costs the row
# its shape but never hides the price.
_BUTTON_NAME_LEN = 30
# The notify button spends one word and a colon on the promise, so the product
# keeps almost as much room as it has on a buy button.
_NOTIFY_NAME_LEN = 28

# How far the ranking runs for the inline list — Telegram's own cap on one
# answer. Lives here because bot/handlers/inline.py imports this module and the
# two have to agree on what "everything you have bought" means: a card from the
# fortieth row asks this file for a discount on a product the top five never
# mention.
INLINE_LIMIT = 50

# How many favourites the screen itself lists.
_ON_SCREEN = 5

# Orders per page. Five of these blocks is a wall of text you get lost in —
# on a phone it is over a screen and a half, and nothing in it stands out. Three
# fit on one screen, and the rest is one tap away.
_ORDERS_PER_PAGE = 3


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


def _as_number(value) -> float:
    """A price as a number, or 0 for anything unparseable."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def order_products(row: dict) -> list[dict]:
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
    return f"• {escape(name)} ×{escape(str(product.get('qty', '')))}"


def format_cached_order(
    row: dict, t: Texts, *, number: int, is_latest: bool = False, expanded: bool = False
) -> str:
    """Format a single cached order (from DB dict) as a text block.

    `number` is the order's position in the whole list, counted across pages. It
    is what the expand button carries, so the two can be matched by eye. Zero
    leaves it off, which is what a card picked out of the inline list needs:
    there is no list around it to be the third of.

    Orders with more than _MAX_INLINE_ITEMS lines are shortened — 22% of orders
    have that many — and the caller offers a button to expand this one.
    """
    source_label = t.order_source_label(row)

    products = order_products(row)
    if not products:
        item_lines = ["-"]
    elif expanded or len(products) <= _MAX_INLINE_ITEMS:
        item_lines = [_item_line(p, t) for p in products]
    else:
        shown = products[:_COLLAPSED_ITEMS]
        item_lines = [_item_line(p, t) for p in shown]
        item_lines.append(
            t.MSG_ORDER_MORE_ITEMS.format(count=len(products) - len(shown))
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

    heading = f"{number}. " if number else ""
    lines = [
        f"<b>{heading}{escape(source_label)}</b>{mark}",
        f"{t.LBL_STATUS}: <b>{status}</b>",
        f"{t.LBL_PRODUCTS}:",
        *item_lines,
        f"{t.LBL_TOTAL}: {total} {currency}",
        f"{t.LBL_DATE}: {escape(date_str)}",
    ]

    tracking = row.get("tracking_code", "")
    if tracking:
        lines.append(f"{t.MSG_ORDER_TRACKING.format(code=texts.tracking_link(tracking))}")

    location_parts = [p for p in (row.get("delivery_city", ""), row.get("receive_point", "")) if p]
    if location_parts:
        location = escape(", ".join(location_parts))
        lines.append(f"{t.MSG_ORDER_LOCATION.format(location=location)}")

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
    if any(len(order_products(row)) > _MAX_INLINE_ITEMS for row in visible):
        header += "\n" + t.MSG_ORDERS_EXPAND_HINT
    header += "\n\n"

    # A page of five collapsed orders is far inside Telegram's 4096, but an
    # expanded order with many items could still push it; keep the guard.
    max_len = 3800
    result_parts: list[str] = []
    current_len = len(header)

    for i, row in enumerate(visible):
        block = format_cached_order(
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
    # First, as on the favourites screen: the whole history as an inline list,
    # with a photo per order, a search that runs as you type, and a button that
    # puts a whole past basket back together (bot/handlers/inline.py). The word
    # after the username is what tells that list to answer with orders.
    builder.button(text=t.BTN_ORDERS_ALL,
                   switch_inline_query_current_chat=f"{t.MSG_INLINE_ORDERS_PREFIX} ")

    visible, page = _page_slice(orders, page)
    start = page * _ORDERS_PER_PAGE

    expand_buttons = 0
    for i, row in enumerate(visible):
        if len(order_products(row)) <= _MAX_INLINE_ITEMS:
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

    # The way into the inline list on its own row, then all expand buttons on
    # one — there are at most _ORDERS_PER_PAGE of them and each is a glyph and
    # a number — then paging. No Menu button: the menu is the keyboard under
    # the input field, always there.
    layout = [1]
    if expand_buttons:
        layout.append(expand_buttons)
    if nav:
        layout.append(len(nav))
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
        for product in order_products(row):
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


async def _refresh_orders(chat_id: int, keycrm: KeyCRMClient) -> None:
    """Fetch fresh orders from APIs and upsert into cache.

    Takes chat_id rather than the phone number and looks the number up here,
    so a spawned refresh never carries it as a task argument — a task argument
    lives in a frame that ends up in the traceback of anything that fails
    below it.

    Bounded by _refresh_semaphore so concurrent refreshes can't overwhelm the
    external APIs during a post-broadcast activity burst.
    """
    phone = await get_user_phone(chat_id)
    if not phone:
        return
    async with _refresh_semaphore:
        await sync_orders(chat_id, phone, keycrm)


# ---------------------------------------------------------------------------
# Screens — built here, opened from the menu keyboard in handlers/menu.py,
# and edited in place by the callbacks further down this file.
# ---------------------------------------------------------------------------

async def orders_screen(
    chat_id: int,
    t: Texts,
    keycrm: KeyCRMClient,
    anchor: Message,
) -> tuple[str, InlineKeyboardMarkup | None]:
    """The orders screen, ready to be sent or edited into place.

    `anchor` is only used to show "typing…" while a cold cache is filled; the
    caller decides whether the result becomes a new message or replaces one.
    """
    phone = await get_user_phone(chat_id)
    if not phone:
        return t.ERR_PHONE_NOT_FOUND, None

    cached = await get_cached_orders(chat_id)
    if cached:
        track(chat_id, "orders_viewed", found=len(cached), cached=True)
        # Fire-and-forget background refresh — but only if the cache is stale,
        # so repeated taps and post-broadcast bursts don't re-hit the APIs.
        if not await _is_cache_fresh(chat_id):
            spawn(_refresh_orders(chat_id, keycrm), name="refresh_orders")
    else:
        await typing(anchor)
        await _refresh_orders(chat_id, keycrm)
        cached = await get_cached_orders(chat_id)
        # Tracked after the fetch, not before: an empty cache says nothing about
        # whether the phone matched, and `found=0` here is exactly the signal
        # that a customer shared their contact and saw nothing.
        track(chat_id, "orders_viewed", found=len(cached), cached=False)

    # §5.5, the customer-facing half of the stalled-sync alert. Silent while the
    # sync is healthy, which is nearly always, and above the list rather than
    # below it so a long history cannot bury it.
    notice = await stale_notice(chat_id, t)
    text = _format_orders_from_cache(cached, t)
    return (
        f"{notice}\n\n{text}" if notice else text,
        _orders_kb(cached, t) if cached else _no_orders_kb(t),
    )


async def favourites_screen(
    chat_id: int,
    t: Texts,
    keycrm: KeyCRMClient,
    anchor: Message,
    website_url: str,
) -> tuple[str, InlineKeyboardMarkup | None]:
    """The favourites screen.

    Computed from the cached orders, which already carry their product lines —
    no extra API call. Falls back to a fetch when the cache is cold so the very
    first visit is not empty.
    """
    phone = await get_user_phone(chat_id)
    if not phone:
        return t.ERR_PHONE_NOT_FOUND, None

    cached = await get_cached_orders(chat_id)
    if not cached:
        await typing(anchor)
        await _refresh_orders(chat_id, keycrm)
        cached = await get_cached_orders(chat_id)

    text, markup, found = await _favourites_view(chat_id, t, cached, website_url)
    track(chat_id, "favourites_viewed", found=found)
    return text, markup


async def _favourites_view(
    chat_id: int, t: Texts, cached: list[dict], website_url: str
) -> tuple[str, InlineKeyboardMarkup, int]:
    """The favourites screen — its text, its buttons, and how many it lists.

    The list *is* the buttons. What used to be five text blocks plus two rows of
    numbered buttons plus two lines of legend explaining the numbers is now one
    button per product carrying its own name and today's price; the message
    itself is a heading and an instruction. Nothing on the screen has to be
    matched to anything else on the screen.

    Built in one place because two handlers draw it: opening the screen, and
    toggling a back-in-stock subscription, which has to redraw so the button
    the customer just pressed changes to reflect what it did.

    Two sources of availability meet here, and they answer different questions.
    The storefront's offer says whether a thing can be bought — which is what
    the buy button needs, and what a customer means by "є". The CRM's stock
    level is what the restock watcher compares against, and it is the fallback
    for a product the storefront has no offer for: roughly a fifth of the
    catalogue, mostly samples and sets that are sold but never listed.
    """
    favourites = favourite_products(cached, limit=_ON_SCREEN)
    if not favourites:
        return (t.MSG_NO_FAVOURITES if cached else t.MSG_NO_ORDERS), _no_orders_kb(t), 0

    offers = await get_offers(str(item.get("sku") or "") for item in favourites)
    levels = await get_stock_levels()
    subscribed = await get_subscribed_skus(chat_id)

    repeated = any(item["orders"] > 1 for item in favourites)
    lines = [t.MSG_FAVOURITES_HEADER if repeated else t.MSG_FAVOURITES_HEADER_ONCE]

    # A product with no offer and no stock figure has no button — there is
    # nothing to press — so it is named in the text instead of disappearing.
    silent = [item for item in favourites
              if _buyable(item, offers) is None and not _is_missing(item, offers, levels)]
    if silent:
        names = ", ".join(texts.product_label(item["name"], 28) for item in silent)
        lines += ["", t.MSG_FAVOURITES_ALSO.format(names=escape(names))]

    return (
        "\n".join(lines),
        _favourites_kb(favourites, offers, levels, subscribed, t, website_url),
        len(favourites),
    )


def _buyable(item: dict, offers: dict[str, Offer]) -> Offer | None:
    """The storefront's offer for this product, if it can be bought right now.

    None covers three different situations on purpose — no sku on the cached
    order line, no offer for that sku, or an offer that is not sellable — and
    the screen treats them the same way: no buy button.
    """
    offer = offers.get(str(item.get("sku") or ""))
    return offer if offer is not None and offer.available else None


def _is_out_of_stock(item: dict, levels: dict[str, int]) -> bool:
    """True only when stock is known and says there is none free to sell.

    An unknown sku is never reported as missing: rows cached before skus were
    stored have none, and claiming "out of stock" for something we simply cannot
    look up would be worse than staying quiet.
    """
    sku = str(item.get("sku") or "")
    return bool(sku) and sku in levels and levels[sku] <= 0


def _is_missing(item: dict, offers: dict[str, Offer], levels: dict[str, int]) -> bool:
    """True when we can say, from one source or the other, that it is gone.

    The storefront wins where it has an opinion: it is what the customer would
    see on the site, and it knows about a product that has units but is
    unpublished. Only where it has no offer at all does the CRM's count answer —
    and where neither knows, the screen says nothing rather than guessing.
    """
    offer = offers.get(str(item.get("sku") or ""))
    if offer is not None:
        return not offer.available
    return _is_out_of_stock(item, levels)


def _favourites_kb(favourites, offers, levels, subscribed, t: Texts,
                   website_url: str) -> InlineKeyboardMarkup:
    """The way into the inline list, one button per product, one for the lot,
    then the discount ask.

    Every button says what it does to which product, so the screen needs no
    legend. One per row: the labels carry a product name and a price, and two of
    those side by side is two lines of wrapped text each.

    The buy buttons are url buttons — the cart link is the whole mechanism, and
    Telegram cannot report a tap on one. That is why the link carries UTM tags:
    the shop's analytics is the only place this can be counted.
    """
    builder = InlineKeyboardBuilder()
    # First, because it is the closest thing there is to what the menu key
    # cannot do. «⭐ Улюблені» is a reply-keyboard key, and such a key can only
    # send its own text — no API writes into the input field, and only an
    # inline button carries switch_inline_query_current_chat. So the panel is
    # one tap from the screen the key opens: this button puts "@bot " in the
    # field, and the client draws the list over the keyboard
    # (bot/handlers/inline.py).
    builder.button(text=t.BTN_FAVOURITES_ALL, switch_inline_query_current_chat="")
    rows = 1
    basket: list[int] = []
    total = 0.0

    for item in favourites:
        label = texts.product_label(item["name"], _BUTTON_NAME_LEN)
        offer = _buyable(item, offers)
        if offer is not None:
            builder.button(
                text=t.BTN_BUY_PRODUCT.format(name=label, price=texts.price_label(offer.price)),
                url=cart_url(website_url, [offer.variant_id], t.lang),
            )
            basket.append(offer.variant_id)
            total += _as_number(offer.price)
            rows += 1
            continue

        sku = str(item.get("sku") or "")
        if not sku or not _is_missing(item, offers, levels):
            continue
        waiting = sku in subscribed
        builder.button(
            # Slightly shorter name here: the promise takes a word up front.
            text=(t.BTN_NOTIFY_WAITING if waiting else t.BTN_NOTIFY_PRODUCT).format(
                name=texts.product_label(item["name"], _NOTIFY_NAME_LEN)
            ),
            callback_data=StockAction(action="unsub" if waiting else "sub", sku=sku),
        )
        rows += 1

    # One basket with everything available in it. Only from two products up:
    # with one it is the button directly above it, worded at greater length.
    if len(basket) > 1:
        builder.button(text=t.BTN_BUY_ALL.format(total=texts.price_label(total)),
                       url=cart_url(website_url, basket, t.lang))
        rows += 1

    builder.button(text=t.BTN_WANT_DISCOUNT, callback_data=DiscountAction(action="ask"))
    rows += 1
    builder.adjust(*([1] * rows))
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

    # One product when the ask came from its card in the inline list, the whole
    # list when it came from the screen. Either way the products are read from
    # this customer's own history, so a forged sku asks for nothing rather than
    # for somebody else's product — and ranked as deep as the list goes, since
    # a card can come from a row the screen never showed.
    favourites = favourite_products(await get_cached_orders(chat_id),
                                    limit=INLINE_LIMIT)
    if callback_data.sku:
        favourites = [f for f in favourites
                      if str(f.get("sku") or "") == callback_data.sku]
    else:
        favourites = favourites[:_ON_SCREEN]
    if not favourites:
        await callback.answer()
        return

    op = operator_texts()
    lines = [op.MSG_DISCOUNT_ADMIN.format(chat_id=chat_id), ""]
    lines += [
        f"• {escape(texts.shorten_name(f['name'], 60))} — "
        f"{op.MSG_FAVOURITE_LINE.format(orders=f['orders'], qty=f['qty'], date=texts.short_date(f['last']))}"
        for f in favourites
    ]
    lines += ["", escape(op.MSG_SUPPORT_REPLY_INSTRUCTION)]
    try:
        sent = await callback.bot.send_message(
            config.support_chat_id, "\n".join(lines), parse_mode="HTML"
        )
        # Same thread mechanism as support: a manager replying to this message
        # reaches the customer. Without it the request carried only the chat_id
        # printed in the text, so a reply landed nowhere unless the manager
        # happened to reply to that exact line — the failure this whole table
        # exists to remove.
        await remember_support_thread([sent.message_id], chat_id)
    except TelegramAPIError as exc:
        # It used to say "passed on to the manager" here whatever happened, and
        # the request was already written down — so a customer was thanked for
        # a request nobody received, and the week-long throttle then refused
        # them a second try. Both halves of that are fixed by the order: record
        # it only once it has actually arrived, and say what happened.
        logger.error("Discount request to chat {} failed: {}",
                     config.support_chat_id, exc)
        await tell_admins_once(
            callback.bot, config.env.admin_ids, "support_relay",
            f"Discount requests are not reaching support: {exc}\n\n"
            f"support_chat_id={config.support_chat_id}. A bot cannot write to a "
            f"user who has never opened it — that account must press Start, or "
            f"the id must name a group the bot is in.",
        )
        await callback.answer(t.MSG_DISCOUNT_FAILED, show_alert=True)
        return

    # Written down only now: what is in this table is what a manager was asked,
    # and it is also what the week-long throttle above reads.
    await add_discount_request(chat_id, json.dumps(
        [{"sku": f["sku"], "name": f["name"], "orders": f["orders"]} for f in favourites],
        ensure_ascii=False,
    ))
    track(chat_id, "discount_requested", products=len(favourites),
          source="card" if callback_data.sku else "screen")

    await callback.answer(t.MSG_DISCOUNT_SENT, show_alert=True)


# Not the ones from a card in the inline list: those carry an inline_message_id
# and no message, and this handler redraws the screen the callback came from.
# bot/handlers/inline.py has their own, and this router is registered first.
@router.callback_query(StockAction.filter(), ~F.inline_message_id)
async def toggle_stock_subscription(
    callback: CallbackQuery,
    callback_data: StockAction,
    config: AppConfig,
    t: Texts,
) -> None:
    """Subscribe to, or unsubscribe from, a product coming back in stock."""
    chat_id = callback.from_user.id
    sku = callback_data.sku

    if callback_data.action == "unsub":
        await remove_stock_subscription(chat_id, sku)
        track(chat_id, "stock_unsubscribed")
        await callback.answer(t.MSG_UNSUBSCRIBED, show_alert=True)
        await _redraw_favourites(callback, chat_id, t, config.website_url)
        return

    # The product name comes from this customer's own cached orders, so a
    # forged sku subscribes to nothing rather than to somebody else's product.
    name = ""
    for row in await get_cached_orders(chat_id):
        for product in order_products(row):
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
    await _redraw_favourites(callback, chat_id, t, config.website_url)


async def _redraw_favourites(callback: CallbackQuery, chat_id: int, t: Texts,
                             website_url: str) -> None:
    """Redraw the favourites screen after a subscription changed.

    Without this the button keeps offering what the customer just did: they tap
    "🔔 2", the pop-up says we will write when it is back, and the button still
    reads 🔔. These buttons exist nowhere else, so the screen the callback came
    from is always the favourites list.
    """
    text, markup, _found = await _favourites_view(
        chat_id, t, await get_cached_orders(chat_id), website_url
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
