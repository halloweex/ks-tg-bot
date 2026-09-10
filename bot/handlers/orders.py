"""Order display handler — show cached orders instantly, refresh in background."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime
from html import escape
from typing import NamedTuple

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.types import (CallbackQuery, InlineKeyboardMarkup,
                           InputMediaPhoto, InputRichBlockPhoto, Message,
                           RichTextBold, RichTextUrl)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from loguru import logger

from core import texts
from core.i18n import Texts, operator_texts
from bot.callbacks import (DiscountAction, MenuAction, OrderAction,
                           SettingsAction, StockAction)
from bot.alerts import tell_admins_once
from bot.analytics import track
from bot.customer import describe
from core.config import AppConfig
from core.domain.offer import Offer
from core.ports.errors import Unavailable
from core.repos.support import (PENDING_LIMIT, add_discount_request,
                                pending_discount_count,
                                pending_discount_request,
                                remember_support_thread)
from core.repos.stock import (add_stock_subscription, get_stock_levels,
                              get_subscribed_skus, remove_stock_subscription)
from core.repos.catalogue import get_offers
from core.repos.orders import (CANCELLED_STATUS_GROUP, get_cached_orders,
                               get_last_sync_time)
from core.repos.uow import SqliteUnitOfWork
from core.repos.users import SqliteCustomerDirectory, get_user_phone
from bot.keyboards import (STYLE_CART, STYLE_LIST, STYLE_UNDO, cart_url,
                           discount_url, shop_url)
from bot import rich
from bot.screen import render, typing
from bot.handlers.delivery import parcel_lines
from bot.sync import stale_notice
from core.adapters.keycrm.client import KeyCRMClient
from core.adapters.novaposhta.client import NovaPoshtaClient
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

# What we ask the storefront for when a favourite becomes a card. There is no
# size control in the rich API — InputRichBlockPhoto takes a photo and a
# caption, and width/height exist only on the map block — so the picture's size
# is decided by which picture we hand over. Telegram draws a small one small:
# the owner compared full-width against this side by side on 2026-09-10 and
# chose this. Same number the inline panel's thumbnails have always used.
_RICH_PHOTO_WIDTH = 200

# Orders per page: one card and nine lines. It used to be three, because three
# full blocks were already a screen and a half; a digest line is forty
# characters, so ten of them fit where three blocks did not.
_ORDERS_PER_PAGE = 10

# How many cancelled orders the tail carries before it says "and N more". They
# are one line each and nobody scrolls a hundred of them; the number is small
# because the tail is weighed against the budget on every candidate order, so
# a long tail costs real orders their place on the screen.
_CANCELLED_IN_TAIL = 10


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

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
    row: dict, t: Texts, *, number: int, is_latest: bool = False,
    expanded: bool = False, rich: bool = True
) -> str:
    """Format a single cached order (from DB dict) as a text block.

    `rich` decides whether the block may wear the real logos — Instagram's on
    the heading, Nova Poshta's on the tracking line. On for the screens, which
    the bot sends itself; off for the card a customer sends by picking a row out
    of the inline list, which is not the bot's message to decorate.

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
    total = texts.price_label(row.get("grand_total", 0))
    currency = escape(t.currency(row.get("currency", "грн")))

    mark = t.MSG_ORDER_LATEST_MARK if is_latest else ""

    heading = f"{number}. " if number else ""
    # Escaped first — an order number comes from the CRM — and only then handed
    # the logo, which is a tag and must survive the escaping rather than be it.
    named = escape(source_label)
    if rich:
        named = texts.with_logo(named, texts.INSTAGRAM, "📸")
    lines = [
        f"<b>{heading}{named}</b>{mark}",
        f"{t.LBL_STATUS}: <b>{status}</b>",
        f"{t.LBL_PRODUCTS}:",
        *item_lines,
        f"{t.LBL_TOTAL}: {total} {currency}",
        f"{t.LBL_DATE}: {escape(date_str)}",
    ]

    tracking = row.get("tracking_code", "")
    if tracking:
        line = t.MSG_ORDER_TRACKING.format(code=texts.tracking_link(tracking))
        lines.append(texts.with_logo(line, texts.NOVA_POSHTA, "🚚") if rich else line)

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


def _status_glyph(row: dict) -> str:
    """One character for where an order stands.

    The digest gives each order a line, and a line has to be scannable without
    being read. Four states are all a customer sorts by: done, on its way,
    being prepared, and never happened.
    """
    if row.get("status_group_id") == CANCELLED_STATUS_GROUP:
        return "❌"
    status = str(row.get("status_name") or "").strip().lower()
    if status in ("completed", "delivered"):
        return "✅"
    if row.get("tracking_code") or status in (
            "in_transit", "departing", "delivered_to_delivery", "pickup"):
        return "🚚"
    return "🕐"


def _qty(product: dict) -> int:
    """How many of one line item, or 0 for a row that cannot say."""
    try:
        return int(product.get("qty") or 0)
    except (TypeError, ValueError):
        return 0


def _digest_line(row: dict, t: Texts) -> str:
    """One order as one line: when, how much, how many things."""
    products = order_products(row)
    return t.MSG_ORDER_DIGEST_LINE.format(
        glyph=_status_glyph(row),
        date=texts.short_date(str(row.get("ordered_at") or "")),
        total=texts.price_label(row.get("grand_total", 0)),
        currency=escape(t.currency(str(row.get("currency") or "грн"))),
        items=t.items(sum(_qty(p) for p in products) or len(products)),
    )


def _split_cancelled(orders: list[dict]) -> tuple[list[dict], list[dict]]:
    """The history a customer is looking for, and the orders that never were."""
    active = [r for r in orders if r.get("status_group_id") != CANCELLED_STATUS_GROUP]
    cancelled = [r for r in orders if r.get("status_group_id") == CANCELLED_STATUS_GROUP]
    return active, cancelled


def _card_row(visible: list[dict], shown_id: int) -> dict | None:
    """Which order is drawn in full: the one asked for, else the newest here."""
    for row in visible:
        if row.get("id") == shown_id:
            return row
    return visible[0] if visible else None


def _format_orders_from_cache(
    orders: list[dict], t: Texts, *, shown_id: int = 0, page: int = 0,
    cancelled: bool = False, expanded: bool = False,
    parcel: list[str] | None = None
) -> str:
    """The digest: one order in full, the rest a line each.

    What this replaced printed every order as six labelled lines — five orders
    came to 42 lines and 901 characters, twenty of which were the words
    "Статус:", "Товари:", "Сума:", "Дата:" — and the verdict on it was that you
    get lost in it. So: the order being looked at is a card, and its neighbours
    are the three facts a history is scanned by.

    `parcel` is what Nova Poshta says about the card's parcel right now, asked
    for by the button on the card. Absent until then, because the screen has to
    open instantly and that answer costs a network call.
    """
    if not orders:
        return t.MSG_NO_ORDERS

    active, cancelled_rows = _split_cancelled(orders)
    visible, page = _page_slice(active, page)
    start = page * _ORDERS_PER_PAGE

    parts = [t.MSG_ORDERS_HEADER]
    if len(active) > _ORDERS_PER_PAGE:
        parts.append(t.MSG_ORDERS_PAGE.format(
            first=start + 1, last=start + len(visible), total=len(active)))

    card = _card_row(visible, shown_id)
    if card is not None:
        # Collapsed like any other block above four items: a card is the detail
        # view, not a reason to open the screen on twenty product names. The
        # keyboard offers to unfold it.
        block = format_cached_order(
            card, t, number=0, is_latest=(card is active[0]), expanded=expanded)
        if parcel:
            block += "\n" + "\n".join(parcel)
        parts += ["", block]

    rest = [row for row in visible if row is not card]
    if rest:
        parts += [""] + [_digest_line(row, t) for row in rest]

    if cancelled and cancelled_rows:
        parts += ["", t.MSG_CANCELLED_HEADER]
        parts += [_digest_line(row, t) for row in cancelled_rows]

    return "\n".join(parts)


def _orders_kb(
    orders: list[dict], t: Texts, *, shown_id: int = 0, page: int = 0,
    cancelled: bool = False, expanded: bool = False, parcel: bool = False,
    rich: bool = False,
) -> InlineKeyboardMarkup:
    """The way into the list, one button per line, the parcel, the folded ones.

    **`rich` drops most of it, because most of it describes a state the rich
    screen does not have.** Measured by driving every button against a rich
    anchor: ten of thirteen left the message byte-identical. There is no card
    in blocks — every order is its own section and the reader folds them
    herself — so `shown_id` names nothing, "show the items" is what the section
    already does, and the parcel button lives inside the order it belongs to.
    Paging goes too: the rich screen is cut by the block budget, not by tens,
    so "older" showed fewer orders than the page before it.

    What survives is what still means something: the search over the whole
    history, the cancelled toggle (blocks honour it), and the menu.

    Every line in the digest has a button that makes it the card, labelled with
    the same glyph and date the line carries — so nothing on the screen has to
    be matched to a number, which is what the old "🔎 3" buttons demanded.

    Paired up where the pairs mean something: the two buttons about the card
    share a row, and so do "show the cancelled ones" and the way to the next
    page. On fifty orders that is the difference between seven rows of keyboard
    and nine, which on a phone is half a screen of buttons under the message.
    """
    builder = InlineKeyboardBuilder()
    # First, as on the favourites screen: the whole history as an inline list,
    # with a photo per order, a search that runs as you type, and a button that
    # puts a whole past basket back together (bot/handlers/inline.py). The word
    # after the username is what tells that list to answer with orders.
    builder.button(text=t.BTN_ORDERS_ALL,
                   switch_inline_query_current_chat=f"{t.MSG_INLINE_ORDERS_PREFIX} ",
                   style=STYLE_LIST)
    layout = [1]

    active, cancelled_rows = _split_cancelled(orders)
    visible, page = _page_slice(active, page)
    card = _card_row(visible, shown_id)
    state = ("c" if cancelled else "") + ("x" if expanded else "")

    # The two about the card, on one row: its own item list, and where it is.
    about_card = 0
    if not rich and card is not None and len(order_products(card)) > _MAX_INLINE_ITEMS:
        builder.button(
            text=(t.BTN_HIDE_ITEMS if expanded else t.BTN_SHOW_ITEMS).format(
                count=len(order_products(card))),
            callback_data=OrderAction(
                action="show", order_id=card.get("id", 0), page=page,
                state=("c" if cancelled else "") + ("" if expanded else "x")),
        )
        about_card += 1
    # Where the parcel is, from Nova Poshta rather than from the shop's record.
    # Only on a card that has a number, and only until it has been asked — the
    # answer arrives by itself a moment after the screen opens, and replaces it.
    if not rich and card is not None and card.get("tracking_code") and not parcel:
        builder.button(
            text=t.BTN_WHERE_PARCEL,
            callback_data=OrderAction(action="track", order_id=card.get("id", 0),
                                      page=page, state=state),
            # The carrier's own mark, on the button that asks the carrier. Same
            # permission as the logos in the text, and the same fallback: a
            # refused icon costs the icon, not the message (bot/middlewares.py).
            icon_custom_emoji_id=texts.NOVA_POSHTA,
        )
        about_card += 1
    if about_card:
        layout.append(about_card)

    others = [] if rich else [row for row in visible if row is not card]
    for row in others:
        builder.button(
            text=f"{_status_glyph(row)} {texts.short_date(str(row.get('ordered_at') or ''))}",
            callback_data=OrderAction(action="show", order_id=row.get("id", 0),
                                      page=page, state=state),
        )
    if others:
        # Three to a row: a glyph and a date is short, and a column of them
        # would be as long as the list they belong to.
        layout += [3] * (len(others) // 3) + ([len(others) % 3] if len(others) % 3 else [])

    # The last row before the menu: the folded orders and the older ones. Both
    # are "show me more of the same", and neither is wide.
    more = 0
    if cancelled_rows:
        builder.button(
            text=(t.BTN_CANCELLED_HIDE if cancelled
                  else t.BTN_CANCELLED_SHOW.format(count=len(cancelled_rows))),
            callback_data=OrderAction(action="show", order_id=shown_id, page=page,
                                      state="" if cancelled else "c"),
        )
        more += 1

    nav: list[tuple[str, int]] = []
    if rich:
        pass          # cut by the budget, not by tens; see the docstring
    elif page > 0:
        nav.append((t.BTN_ORDERS_NEWER, page - 1))
    if not rich and (page + 1) * _ORDERS_PER_PAGE < len(active):
        nav.append((t.BTN_ORDERS_OLDER, page + 1))
    for label, target in nav:
        # The card is not carried across pages: it names an order that is not
        # on the new one, so the newest of that page becomes the card instead.
        builder.button(text=label,
                       callback_data=OrderAction(action="show", order_id=0,
                                                 page=target, state=state))
    more += len(nav)
    if more:
        layout.append(more)

    # Last row, and on every screen the menu opens: a menu entry replaces the
    # menu message with the section, so without this the only way back is the
    # keyboard below the input field — which answers with a new message.
    builder.button(text=t.BTN_MENU, callback_data=MenuAction(action="menu"))
    layout.append(1)

    builder.adjust(*layout)
    return builder.as_markup()


def _no_phone_kb(t: Texts) -> InlineKeyboardMarkup:
    """The way out of the screen that had none.

    Both order screens answered "no phone stored" with a bare string and no
    keyboard, which is the only dead end left in the bot: nothing to tap, and
    the reply keyboard underneath cannot help because every one of its keys
    lands back on a screen that needs the number.

    The first button is the actual fix — it starts the same phone-change flow
    as Settings, which sends the share-contact keyboard. The second is for the
    likelier cause, an order under a different number than her Telegram, which
    only a person can join up.
    """
    builder = InlineKeyboardBuilder()
    builder.button(text=t.BTN_CHANGE_PHONE,
                   callback_data=SettingsAction(action="phone"))
    builder.button(text=t.BTN_SUPPORT, callback_data=MenuAction(action="support"))
    builder.button(text=t.BTN_MENU, callback_data=MenuAction(action="menu"))
    builder.adjust(1)
    return builder.as_markup()


def _anchor_is_rich(callback: CallbackQuery) -> bool:
    """Whether the screen this tap came from is drawn in blocks.

    The redraw handlers build one keyboard and hand it to `render`, which picks
    the shape by the anchor — so without asking the same question here the slab
    under a rich screen stays the plain screen's, and most of its buttons
    describe a state the message does not have.
    """
    return getattr(callback.message, "rich_message", None) is not None


def _no_orders_kb(t: Texts, config: AppConfig | None = None) -> InlineKeyboardMarkup:
    """The first-order discount, support, and the way back.

    A customer who just shared their contact and got "no orders" has nowhere to
    go otherwise, and the two things worth offering them are opposite: a reason
    to buy something, and a person to ask if they believe they already have.
    The second most likely cause of an empty screen — the order sits under a
    different phone than their Telegram — is something only a manager can fix.

    The discount button carries the code where one is configured. There is no
    default code in the code, and there never should be: the bot hands out a
    link that applies one, it does not invent discounts.

    Where none is configured the shop itself takes that seat. Saying "time to
    make your first order" and then offering only a manager left the screen
    inviting a purchase it gave no way to make — and the code has been pending
    on the shop's owner for weeks, which is too long for a customer to wait for
    a way in. The promo still comes from a person; the catalogue does not have
    to.
    """
    builder = InlineKeyboardBuilder()
    if config is not None and config.first_order_code:
        builder.button(
            text=t.BTN_FIRST_ORDER,
            url=discount_url(config.website_url, config.first_order_code, t.lang),
            style=STYLE_CART,
        )
    elif config is not None and config.website_url:
        builder.button(
            text=t.BTN_FIRST_ORDER_SHOP,
            url=shop_url(config.website_url, t.lang),
            style=STYLE_CART,
        )
    builder.button(text=t.BTN_SUPPORT, callback_data=MenuAction(action="support"))
    builder.button(text=t.BTN_MENU, callback_data=MenuAction(action="menu"))
    builder.adjust(1)
    return builder.as_markup()


def first_order_kb(t: Texts, config: AppConfig) -> InlineKeyboardMarkup:
    """The way to claim it: the link with the code, or the person who has one.

    The invitation promises this discount unconditionally, so the arrival has
    to honour it unconditionally too. With a code that is one tap; without one
    it is a manager, which is slower and still a kept promise.
    """
    builder = InlineKeyboardBuilder()
    if config.first_order_code:
        builder.button(
            text=t.BTN_FIRST_ORDER,
            url=discount_url(config.website_url, config.first_order_code, t.lang),
            style=STYLE_CART,
        )
    else:
        builder.button(text=t.BTN_SUPPORT, callback_data=MenuAction(action="support"))
    return builder.as_markup()


def first_order_offer(t: Texts, config: AppConfig) -> str:
    """The offer as a paragraph, or "" when no code is configured.

    Its own function because two screens and the end of registration all show
    the same thing, and a customer who has seen it once should not read three
    different versions of it.
    """
    if not config.first_order_reward:
        return ""
    offer = t.MSG_FIRST_ORDER.format(reward=escape(config.first_order_reward))
    # The ending has to match the button underneath. With a code the link
    # carries it; without one the link is the plain shop, and telling someone
    # the discount is already in it sends them to a checkout that disagrees.
    return offer + (t.MSG_FIRST_ORDER_IN_LINK if config.first_order_code
                    else t.MSG_FIRST_ORDER_BY_HAND)


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
        # The handles are built here, as in cmd_stats and _queue_broadcast: no
        # composition root exists yet, so this is where it is known which engine
        # is underneath. SqliteUnitOfWork is passed as the factory itself — the
        # port is a callable taking user_id, and the class is one.
        await sync_orders(chat_id, phone, keycrm,
                          SqliteCustomerDirectory(), SqliteUnitOfWork)


# ---------------------------------------------------------------------------
# Screens — built here, opened from the menu keyboard in handlers/menu.py,
# and edited in place by the callbacks further down this file.
# ---------------------------------------------------------------------------

class Screen(NamedTuple):
    """One screen in both shapes, because a reader may only get one of them.

    `blocks` is None where the rich form would add nothing — the empty and
    error screens are a paragraph and a button, and a paragraph is a paragraph
    in either shape.
    """

    text: str
    markup: InlineKeyboardMarkup | None
    blocks: list | None = None


async def orders_screen(
    chat_id: int,
    t: Texts,
    keycrm: KeyCRMClient,
    anchor: Message,
    config: AppConfig | None = None,
) -> Screen:
    """The orders screen, ready to be sent or edited into place.

    `anchor` is only used to show "typing…" while a cold cache is filled; the
    caller decides whether the result becomes a new message or replaces one.
    """
    phone = await get_user_phone(chat_id)
    if not phone:
        return Screen(t.MSG_NO_PHONE_YET, _no_phone_kb(t))

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
    if not cached and config is not None:
        # Nothing to show is the one moment a first-order discount is exactly
        # the right thing to say.
        offer = first_order_offer(t, config)
        if offer:
            text = f"{text}\n\n{offer}"
    # The gate is gone: the owner decided on 2026-09-10 that every customer is
    # assumed to be on a current Telegram (docs/rich-messages.md). It stood
    # while the screen was raw and while nobody had looked at an older client;
    # the first is fixed and the second is now a decision rather than an
    # unknown.
    #
    # What the gate bought in the meantime: twenty-six review findings, a hole
    # in the gate itself, and three counters that lied — all met by an admin
    # rather than by a customer.
    return Screen(
        f"{notice}\n\n{text}" if notice else text,
        _orders_kb(cached, t, rich=bool(cached))
        if cached else _no_orders_kb(t, config),
        rich_orders_blocks(cached, t, notice=notice) if cached else None,
    )


# The parcel lookup in flight for a chat, so the next one can cancel it. Without
# this, a customer who opens the screen and immediately taps another order gets
# the first lookup landing afterwards and putting the old card back.
_parcel_tasks: dict[int, asyncio.Task] = {}


def follow_up_parcel(
    sent: Message | None, chat_id: int, t: Texts,
    novaposhta: NovaPoshtaClient | None, *, shown_id: int = 0, page: int = 0,
    cancelled: bool = False, expanded: bool = False,
) -> None:
    """Ask Nova Poshta where the card's parcel is, and put it there.

    In the background, after the screen is already on the customer's phone: the
    lookup takes a second or three and the orders screen is the one that has to
    open instantly. So it opens from the cache, and the live line appears in it
    a moment later — the same trick the screen already uses to refresh itself
    from the CRM.

    Nothing here is awaited by the handler. A parcel that cannot be looked up
    leaves the screen exactly as it was, with the button still offering to ask.
    """
    if sent is None or novaposhta is None:
        return
    previous = _parcel_tasks.pop(chat_id, None)
    if previous is not None and not previous.done():
        previous.cancel()
    task = spawn(_fill_in_parcel(sent, chat_id, t, novaposhta, shown_id=shown_id,
                                 page=page, cancelled=cancelled, expanded=expanded),
                 name="parcel_status")
    _parcel_tasks[chat_id] = task
    task.add_done_callback(lambda done: _parcel_tasks.pop(chat_id, None)
                           if _parcel_tasks.get(chat_id) is done else None)


async def _fill_in_parcel(
    sent: Message, chat_id: int, t: Texts, novaposhta: NovaPoshtaClient, *,
    shown_id: int, page: int, cancelled: bool, expanded: bool,
) -> None:
    """The background half of follow_up_parcel."""
    cached = await get_cached_orders(chat_id)
    active, _cancelled = _split_cancelled(cached)
    visible, page = _page_slice(active, page)
    card = _card_row(visible, shown_id)
    ttn = str((card or {}).get("tracking_code") or "")
    if not ttn:
        return

    # The number authorises the lookup: Nova Poshta answers a TTN in full only
    # to the phone that sent or receives the parcel.
    phone = await get_user_phone(chat_id)
    if not phone:
        return

    found = await novaposhta.track_many([ttn], phone)
    info = found.get(ttn)
    lines = parcel_lines(card, info, t)
    if not lines:
        return

    try:
        if getattr(sent, "rich_message", None) is not None:
            # The screen this is filling in went out rich, so it has to come
            # back rich: plain text here would succeed and take the blocks with
            # it (docs/rich-messages.md). Unescaped, because a block's text is
            # structured rather than parsed.
            await rich.edit(
                sent,
                rich_orders_blocks(
                    cached, t, cancelled=cancelled, page=page,
                    parcels={card.get("id", 0): parcel_lines(card, info, t,
                                                             as_html=False)}),
                # Without this the edit goes out with no reply_markup at all,
                # and editMessageText reads that as "take the keyboard away":
                # the slab vanished a second after the screen opened.
                reply_markup=_orders_kb(cached, t, shown_id=card.get("id", 0),
                                        page=page, cancelled=cancelled,
                                        expanded=expanded, parcel=True,
                                        rich=True))
        else:
            await sent.edit_text(
                _format_orders_from_cache(cached, t, shown_id=card.get("id", 0),
                                          page=page, cancelled=cancelled,
                                          expanded=expanded, parcel=lines),
                reply_markup=_orders_kb(cached, t, shown_id=card.get("id", 0),
                                        page=page, cancelled=cancelled,
                                        expanded=expanded, parcel=True),
            )
    except Unavailable as exc:
        # Nobody asked for this one: it fills itself in a second after the
        # screen opens. The screen is already drawn and correct without it, so
        # a carrier that is down costs the extra line and nothing else — the
        # apology belongs to the tap on «Де посилка?», not to a background
        # errand the customer never started.
        logger.info("Parcel status not filled in, carrier unavailable: {}", exc)
    except TelegramAPIError as exc:
        # The customer has moved on, or the screen already says this. Neither
        # is worth more than a line in the debug log.
        logger.debug("Could not put the parcel status on the screen: {}", exc)


async def favourites_screen(
    chat_id: int,
    t: Texts,
    keycrm: KeyCRMClient,
    anchor: Message,
    website_url: str,
    config: AppConfig | None = None,
) -> Screen:
    """The favourites screen.

    Computed from the cached orders, which already carry their product lines —
    no extra API call. Falls back to a fetch when the cache is cold so the very
    first visit is not empty.
    """
    phone = await get_user_phone(chat_id)
    if not phone:
        return Screen(t.MSG_NO_PHONE_YET, _no_phone_kb(t))

    cached = await get_cached_orders(chat_id)
    if not cached:
        await typing(anchor)
        await _refresh_orders(chat_id, keycrm)
        cached = await get_cached_orders(chat_id)

    text, markup, blocks, found = await _favourites_view(
        chat_id, t, cached, website_url, config)
    track(chat_id, "favourites_viewed", found=found)
    return Screen(text, markup, blocks)


async def _favourites_view(
    chat_id: int, t: Texts, cached: list[dict], website_url: str,
    config: AppConfig | None = None,
) -> tuple[str, InlineKeyboardMarkup, list | None, int]:
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
        empty = t.MSG_NO_FAVOURITES if cached else t.MSG_NO_ORDERS
        offer = first_order_offer(t, config) if config is not None and not cached else ""
        return (f"{empty}\n\n{offer}" if offer else empty,
                _no_orders_kb(t, config), None, 0)

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

    asked = await pending_discount_request(chat_id)
    return (
        "\n".join(lines),
        _favourites_kb(favourites, offers, levels, subscribed, t, website_url,
                       repeated, asked, rich=True),
        rich_favourites_blocks(favourites, offers, levels, subscribed, t,
                               website_url, repeated=repeated, asked=asked),
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


def _photo_at(url: str, width: int = _RICH_PHOTO_WIDTH) -> str:
    """The same picture, asked for smaller."""
    joiner = "&" if "?" in url else "?"
    return f"{url}{joiner}width={width}"


def rich_favourites_blocks(favourites, offers, levels, subscribed, t: Texts,
                           website_url: str, *, repeated: bool = True,
                           asked: bool = False) -> list:
    """Favourites as cards, each product its own picture, name, price and button.

    The plain screen is a heading and a column of buttons, because a button was
    the only place a product's name and price could sit together. Here they sit
    beside the product's own photo, which is the thing the inline panel existed
    to provide and the reason it was one tap further in.

    What it costs, and it was weighed: media in a rich message can only be its
    own block, so there is no row with a thumbnail on the left. Five products
    are five full-width cards, a longer screen that scrolls differently. The
    owner looked at it against the alternatives on 2026-09-10 and chose this,
    with the picture asked of the storefront at _RICH_PHOTO_WIDTH.
    """
    blocks: list = [rich.heading(
        t.MSG_FAVOURITES_HEADER if repeated else t.MSG_FAVOURITES_HEADER_ONCE,
        size=2)]

    for item in favourites:
        offer = _buyable(item, offers)
        picture = offers.get(str(item.get("sku") or ""))
        if picture is not None and picture.image_url:
            blocks.append(InputRichBlockPhoto(
                # parse_mode spelled out because the field defaults to a
                # Default(...) sentinel that only resolves inside a Bot, and
                # anything that serialises these blocks outside one — the
                # budget counters, every test — dies on it. There is no caption
                # here to parse anyway.
                photo=InputMediaPhoto(media=_photo_at(picture.image_url),
                                      parse_mode=None,
                                      show_caption_above_media=None)))

        blocks.append(rich.para([RichTextBold(
            text=texts.product_label(item["name"], _BUTTON_NAME_LEN))]))

        if offer is not None:
            blocks.append(rich.para(t.MSG_FAVOURITE_PRICE.format(
                price=texts.price_label(offer.price))))
            blocks.append(rich.buttons(rich.button(
                t.BTN_BUY_ONE,
                url=cart_url(website_url, [offer.variant_id], t.lang),
                style=STYLE_CART)))
        else:
            sku = str(item.get("sku") or "")
            if sku and _is_missing(item, offers, levels):
                waiting = sku in subscribed
                blocks.append(rich.buttons(rich.button(
                    t.BTN_NOTIFY_WAITING_SHORT if waiting else t.BTN_NOTIFY_SHORT,
                    callback_data=StockAction(
                        action="unsub" if waiting else "sub", sku=sku).pack(),
                    style=STYLE_UNDO if waiting else None)))
        blocks.append(rich.divider())

    # The lot, and the ask. Both are about the whole screen rather than about
    # one product, so they sit under the rule rather than inside a card.
    basket = [o.variant_id for o in
              (_buyable(i, offers) for i in favourites) if o is not None]
    total = sum(_as_number(o.price) for o in
                (_buyable(i, offers) for i in favourites) if o is not None)
    tail: list = []
    if len(basket) > 1:
        tail.append(rich.button(
            t.BTN_BUY_ALL.format(total=texts.price_label(total)),
            url=cart_url(website_url, basket, t.lang), style=STYLE_CART))
    tail.append(rich.button(
        t.BTN_DISCOUNT_ASKED if asked
        else (t.BTN_WANT_DISCOUNT if repeated else t.BTN_WANT_DISCOUNT_PLAIN),
        callback_data=DiscountAction(action="ask").pack()))
    blocks.append(rich.buttons(*tail))
    return blocks


def _favourites_kb(favourites, offers, levels, subscribed, t: Texts,
                   website_url: str, repeated: bool = True,
                   asked: bool = False, rich: bool = False) -> InlineKeyboardMarkup:
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
    builder.button(text=t.BTN_FAVOURITES_ALL, switch_inline_query_current_chat="",
                   style=STYLE_LIST)
    rows = 1
    basket: list[int] = []
    total = 0.0

    # On a rich screen every one of these lives inside the card it belongs to,
    # beside the product's own picture — which is the whole point of the move.
    # Leaving them here as well would be the same button twice, once with the
    # name it acts on and once without.
    for item in ([] if rich else favourites):
        label = texts.product_label(item["name"], _BUTTON_NAME_LEN)
        offer = _buyable(item, offers)
        if offer is not None:
            builder.button(
                text=t.BTN_BUY_PRODUCT.format(name=label, price=texts.price_label(offer.price)),
                url=cart_url(website_url, [offer.variant_id], t.lang),
                style=STYLE_CART,
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
            # Red only in the waiting state, where pressing it cancels the
            # promise the customer asked for. Offering that promise is not an
            # undo, so the other half of this toggle stays uncoloured.
            style=STYLE_UNDO if waiting else None,
        )
        rows += 1

    # One basket with everything available in it. Only from two products up:
    # with one it is the button directly above it, worded at greater length.
    if not rich and len(basket) > 1:
        builder.button(text=t.BTN_BUY_ALL.format(total=texts.price_label(total)),
                       url=cart_url(website_url, basket, t.lang), style=STYLE_CART)
        rows += 1

    # Asked already, and nobody has answered yet: the button says so and wears
    # the colour every other "you have done this" button wears. It stays
    # pressable — tapping it repeats the status instead of sending a second
    # copy of the same question.
    #
    # Otherwise, the same fact the header is chosen by: with nothing bought
    # twice, «на ці товари» would be asking for a discount on the strength of a
    # habit the customer does not have yet.
    if rich:
        pass          # both live under the rule in the blocks
    elif asked:
        builder.button(text=t.BTN_DISCOUNT_ASKED,
                       callback_data=DiscountAction(action="ask"),
                       style=STYLE_UNDO)
        rows += 1
    else:
        builder.button(
            text=t.BTN_WANT_DISCOUNT if repeated else t.BTN_WANT_DISCOUNT_PLAIN,
            callback_data=DiscountAction(action="ask"),
        )
        rows += 1
    # The way back, for the same reason it is on every other screen.
    builder.button(text=t.BTN_MENU, callback_data=MenuAction(action="menu"))
    rows += 1
    builder.adjust(*([1] * rows))
    return builder.as_markup()


async def perform_discount_ask(callback: CallbackQuery, sku: str,
                               config: AppConfig, t: Texts) -> bool:
    """Pass a discount request, with the customer's favourites, to a manager.

    Shared by the two surfaces that can ask — the favourites screen and a card
    in the inline list — because only the redraw afterwards differs: a screen
    is a message this bot can edit, a card is an inline_message_id. Returns
    whether the ask was recorded, which is what tells the caller there is a
    button to redraw.

    Deliberately not an automatically issued code: there is no discount policy
    yet, and the bot inventing one would commit the business to it. The manager
    answers through the existing support relay, which is why the chat_id line is
    formatted the same way — replying to it routes back to this customer.
    """
    chat_id = callback.from_user.id

    # Answered as a pop-up rather than a message: the confirmation belongs to
    # the tap, and the favourites list the customer is looking at should stay
    # where it is.
    #
    # Scoped to the product asked about. The old rule was one ask a week per
    # customer, which meant a question about a cream also refused every other
    # product — and said "already in hand" for seven days whether or not
    # anyone had read it. Now only the same ask is refused, only while it is
    # actually unanswered, and the cap below is what keeps a long list from
    # becoming a long queue.
    if await pending_discount_request(chat_id, sku):
        await callback.answer(t.MSG_DISCOUNT_ALREADY, show_alert=True)
        return False
    if await pending_discount_count(chat_id) >= PENDING_LIMIT:
        await callback.answer(t.MSG_DISCOUNT_MANY, show_alert=True)
        return False

    # One product when the ask came from its card in the inline list, the whole
    # list when it came from the screen. Either way the products are read from
    # this customer's own history, so a forged sku asks for nothing rather than
    # for somebody else's product — and ranked as deep as the list goes, since
    # a card can come from a row the screen never showed.
    favourites = favourite_products(await get_cached_orders(chat_id),
                                    limit=INLINE_LIMIT)
    if sku:
        favourites = [f for f in favourites
                      if str(f.get("sku") or "") == sku]
    else:
        favourites = favourites[:_ON_SCREEN]
    if not favourites:
        await callback.answer()
        return False

    op = operator_texts()
    lines = [op.MSG_DISCOUNT_ADMIN.format(
        who=await describe(callback.from_user, chat_id)), ""]
    lines += [
        f"• {escape(texts.shorten_name(f['name'], 60))} — "
        f"{op.MSG_FAVOURITE_LINE.format(orders=f['orders'], qty=f['qty'], date=texts.short_date(f['last']))}"
        for f in favourites
    ]
    lines += ["", escape(op.MSG_SUPPORT_REPLY_INSTRUCTION)]
    text = "\n".join(lines)
    try:
        sent = await callback.bot.send_message(
            config.support_chat_id, text, parse_mode="HTML"
        )
        # The same request, to every admin as well. One personal account is a
        # single point of failure — it went silent for half a day on 25.08 —
        # and a discount ask is the one thing here nobody else can see was
        # made. Copies are best-effort: the customer's answer depends on the
        # support chat alone, so an admin who has never opened the bot cannot
        # turn their own missing copy into a failed request.
        ids = [sent.message_id]
        for admin_id in config.env.admin_ids:
            if admin_id == config.support_chat_id:
                continue
            try:
                copy = await callback.bot.send_message(admin_id, text,
                                                       parse_mode="HTML")
            except TelegramAPIError as exc:  # noqa: PERF203
                logger.warning("Discount copy to admin {} failed: {}",
                               admin_id, exc)
                continue
            ids.append(copy.message_id)

        # Same thread mechanism as support: a manager replying to any of these
        # messages reaches the customer. Without it the request carried only
        # the chat_id printed in the text, so a reply landed nowhere unless the
        # manager happened to reply to that exact line — the failure this whole
        # table exists to remove. Every copy is registered, so whoever sees the
        # request first can be the one who answers it.
        await remember_support_thread(ids, chat_id)
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
        return False

    # Written down only now: what is in this table is what a manager was asked,
    # and it is also what the week-long throttle above reads.
    await add_discount_request(
        chat_id,
        json.dumps(
            [{"sku": f["sku"], "name": f["name"], "orders": f["orders"]}
             for f in favourites],
            ensure_ascii=False,
        ),
        sku=sku,
        # What a manager replies to. Their answer closes this ask, and the
        # customer can raise it again the same minute if they need to.
        thread_message_id=sent.message_id,
    )
    track(chat_id, "discount_requested", products=len(favourites),
          source="card" if sku else "screen")

    await callback.answer(t.MSG_DISCOUNT_SENT, show_alert=True)
    return True


# Not the ones from a card: those carry an inline_message_id and no message,
# and the redraw below reaches for the screen this callback came from. The card
# has its own handler in bot/handlers/inline.py, and this router is registered
# first.
@router.callback_query(DiscountAction.filter(F.action == "ask"),
                       ~F.inline_message_id)
async def request_discount(
    callback: CallbackQuery,
    callback_data: DiscountAction,
    config: AppConfig,
    t: Texts,
) -> None:
    """The ask from the favourites screen, and the tick it leaves behind."""
    if await perform_discount_ask(callback, callback_data.sku, config, t):
        await _redraw_favourites(callback, callback.from_user.id, t,
                                 config.website_url)


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
    text, markup, blocks, _found = await _favourites_view(
        chat_id, t, await get_cached_orders(chat_id), website_url
    )
    await render(callback, text, markup, blocks=blocks)


@router.callback_query(OrderAction.filter(F.action.in_({"show", "items"})))
async def show_order(
    callback: CallbackQuery,
    callback_data: OrderAction,
    novaposhta: NovaPoshtaClient | None,
    t: Texts,
) -> None:
    """Draw a different order as the card, or fold the cancelled ones away.

    One handler for both because they are the same redraw with different state,
    and "items" is here so the buttons on a screen sent before the digest
    existed still do something sensible rather than nothing.

    The list is re-read for the caller's own chat, so the id in the callback can
    only ever pick one of their orders — a forged id falls back to the newest.
    """
    await callback.answer()
    track(callback.from_user.id, "order_card_opened")

    cached = await get_cached_orders(callback.from_user.id)
    if not cached:
        return

    sent = await render(
        callback,
        _format_orders_from_cache(cached, t, shown_id=callback_data.order_id,
                                  page=callback_data.page,
                                  cancelled="c" in callback_data.state,
                                  expanded="x" in callback_data.state),
        _orders_kb(cached, t, shown_id=callback_data.order_id,
                   page=callback_data.page,
                   cancelled="c" in callback_data.state,
                   expanded="x" in callback_data.state,
                   rich=_anchor_is_rich(callback)),
        # The entrance the first attempt missed. Every button in the slab under
        # a rich screen is one of these, so without blocks here the first tap
        # on any neighbouring order wrote plain text over the blocks and
        # destroyed them — the exact failure the six-at-once move exists to
        # prevent, left in by claiming six and delivering five.
        blocks=rich_orders_blocks(cached, t, page=callback_data.page,
                                  cancelled="c" in callback_data.state),
    )
    # Whichever order became the card, its parcel is looked up the same way.
    follow_up_parcel(sent, callback.from_user.id, t, novaposhta,
                     shown_id=callback_data.order_id, page=callback_data.page,
                     cancelled="c" in callback_data.state,
                     expanded="x" in callback_data.state)


@router.callback_query(OrderAction.filter(F.action == "track"))
async def track_parcel(
    callback: CallbackQuery,
    callback_data: OrderAction,
    novaposhta: NovaPoshtaClient | None,
    t: Texts,
) -> None:
    """Ask Nova Poshta where this parcel is, and put the answer on the card.

    This is the screen that used to be its own menu entry. It is one lookup for
    one parcel, asked for by a tap, rather than a second place in the menu that
    answers what a customer thinks of as the same question — and the screen
    still opens instantly, because nothing here happens until the button.
    """
    chat_id = callback.from_user.id
    await callback.answer()

    cached = await get_cached_orders(chat_id)
    row = next((r for r in cached if r.get("id") == callback_data.order_id), None)
    if row is None or not row.get("tracking_code"):
        return

    phone = await get_user_phone(chat_id)
    parcel: list[str] = []
    if novaposhta and phone:
        await typing(callback.message)
        # The number authorises the lookup: Nova Poshta answers a TTN in full
        # only to the phone that sent or receives it.
        found = await novaposhta.track_many([row["tracking_code"]], phone)
        info = found.get(row["tracking_code"])
    else:
        info = None
    parcel = parcel_lines(row, info, t)
    track(chat_id, "parcel_tracked", found=bool(parcel))

    await render(
        callback,
        _format_orders_from_cache(cached, t, shown_id=callback_data.order_id,
                                  page=callback_data.page,
                                  cancelled="c" in callback_data.state,
                                  expanded="x" in callback_data.state,
                                  parcel=parcel),
        _orders_kb(cached, t, shown_id=callback_data.order_id,
                   page=callback_data.page,
                   cancelled="c" in callback_data.state,
                   expanded="x" in callback_data.state, parcel=True,
                   rich=_anchor_is_rich(callback)),
        # Unescaped for the blocks: their text is structured, not parsed.
        blocks=rich_orders_blocks(
            cached, t, page=callback_data.page,
            cancelled="c" in callback_data.state,
            parcels={callback_data.order_id: parcel_lines(row, info, t,
                                                          as_html=False)}),
    )


# ---------------------------------------------------------------------------
# The same screen as blocks.
#
# Everything above this line is shaped by two limits that Bot API 10.1 removed:
# 4096 characters, and buttons that can only sit in a slab under the whole
# message. That is where the digest came from — one order as a card, its
# neighbours as one line each — and where `shown_id` came from, which exists so
# that exactly one order can be unfolded at a time.
#
# With blocks every order is its own collapsible section carrying its own
# buttons, so none of that state is needed: the client folds and unfolds, and
# the bot is not asked. The plain builders stay untouched beside this one and
# are what actually goes out whenever Telegram refuses the rich form
# (bot/rich.py::send).
# ---------------------------------------------------------------------------

def _order_summary(row: dict, t: Texts) -> list:
    """The one line that stays visible when the section is folded.

    The three facts a history is scanned by, which is the same judgement the
    digest line was built on — it is only the mechanism underneath that changed.
    """
    return [
        f"{_status_glyph(row)} {t.order_source_label(row)} · ",
        RichTextBold(text=f"{texts.price_label(row.get('grand_total', 0))} "
                          f"{t.currency(str(row.get('currency') or 'грн'))}"),
        f" · {texts.short_date(str(row.get('ordered_at') or ''))}",
    ]


def _order_details(row: dict, t: Texts, *, parcel: list[str] | None = None) -> list:
    """What unfolding one order shows."""
    blocks: list = [rich.para([f"{t.LBL_STATUS}: ",
                               RichTextBold(text=t.status(row.get("status_name", "")) or "-")])]

    products = order_products(row)
    if products:
        # Longer than the plain screen allows: the 40-character cut exists
        # because a line there competes with five other labelled lines inside
        # 4096 characters. A list item competes with nothing.
        blocks.append(rich.bullets([
            f"{texts.product_label(str(p.get('name', '')), 90)} ×{_qty(p) or 1}"
            for p in products
        ]))

    tracking = str(row.get("tracking_code") or "")
    if tracking:
        blocks.append(rich.para([
            "🚚 ТТН: ",
            RichTextUrl(text=tracking, url=texts.tracking_url(tracking)),
        ]))
    if parcel:
        blocks += [rich.para(line) for line in parcel]

    where = [p for p in (row.get("delivery_city", ""), row.get("receive_point", "")) if p]
    if where:
        blocks.append(rich.para(f"📍 {', '.join(where)}"))

    # The button belongs to the order it asks about, which is the whole point:
    # before this it sat in the slab below and had to carry an order id so the
    # screen could tell which parcel was meant.
    if tracking and not parcel:
        blocks.append(rich.buttons(rich.button(
            t.BTN_WHERE_PARCEL,
            callback_data=OrderAction(action="track", order_id=row.get("id", 0),
                                      page=0, state="").pack())))
    return blocks


def rich_orders_blocks(orders: list[dict], t: Texts, *,
                       parcels: dict[int, list[str]] | None = None,
                       cancelled: bool = False, page: int = 0,
                       notice: str = "") -> list:
    # NOTE: `parcels` must come from parcel_lines(..., as_html=False). A block's
    # text is structured, not parsed, so an escaped apostrophe arrives as the
    # literal "&#x27;". Nothing here can tell the two apart, which is why it is
    # the caller's contract and why it is written down.
    """The orders screen as blocks: every order, folded, newest open.

    No paging and no card: the budget that forced both is gone. What survives
    is a cap — three of them, `rich.fits` — and it says how many orders it left
    out rather than trimming in silence.

    The tail of the screen is reserved before the loop rather than measured
    after it: the "showing N of M" line, the cancelled heading and its orders
    all go on *after* the last section, and a check that ignores them can fill
    the budget exactly and then overrun it.
    """
    if not orders:
        return [rich.para(t.MSG_NO_ORDERS)]

    active, cancelled_rows = _split_cancelled(orders)
    # The same two switches the plain screen has, and for the same reason: the
    # slab below carries their buttons, and a button that redraws the screen
    # without changing it is worse than no button — Telegram answers "message
    # is not modified" and the tap reads as broken.
    if not cancelled:
        cancelled_rows = []
    if page:
        active, _ = _page_slice(active, page)
    blocks: list = [rich.heading(t.MSG_ORDERS_TITLE, size=2)]
    # §5.5, the customer-facing half of the stalled-sync alert. The plain
    # screen has carried it since it was written; the rich one dropped it on
    # the floor, so the warning was dead for everyone modern enough to see
    # blocks — which, after today's decision, is everyone. Above the list, for
    # the same reason as in the plain form: a long history must not bury it.
    if notice:
        blocks.append(rich.para(notice))
    parcels = parcels or {}

    # Everything that goes on after the loop, weighed with every candidate.
    # Reserved rather than measured afterwards, because a check that ignores
    # the tail can fill the budget exactly and then overrun it. The "showing N
    # of M" line is reserved unconditionally — it appears only when the cap
    # bites, which is the one case where the reservation matters.
    tail: list = [rich.para(t.MSG_ORDERS_PAGE.format(first=1, last=1, total=1))]
    if cancelled_rows:
        # Capped, because it is not a footnote: a customer with a few hundred
        # cancelled orders had a tail of six hundred blocks, which crowded out
        # every real order — the screen came back with nought sections, a line
        # reading "Показано 1–0 з 10", and 694 blocks against a ceiling of 500,
        # so Telegram refused it and she got the plain screen anyway. Every
        # failure the budget exists to prevent, in one screen.
        shown_cancelled = cancelled_rows[:_CANCELLED_IN_TAIL]
        tail += [rich.divider(), rich.heading(t.MSG_CANCELLED_HEADER, size=3)]
        tail += [rich.para(_digest_line(row, t)) for row in shown_cancelled]
        if len(cancelled_rows) > len(shown_cancelled):
            tail.append(rich.para(t.MSG_ORDER_MORE_ITEMS.format(
                count=len(cancelled_rows) - len(shown_cancelled))))
    tail.append(rich.buttons(rich.button(
        t.BTN_MENU, callback_data=MenuAction(action="menu").pack())))

    shown = 0
    for row in active:
        section = rich.details(
            _order_summary(row, t),
            _order_details(row, t, parcel=parcels.get(row.get("id", 0))),
            # Open the one being answered about, else the newest. The parcel
            # lookup redraws the whole screen with one section's lines filled
            # in, and pinning this to active[0] delivered that answer into a
            # folded section: she tapped "Де посилка?", the screen redrew, and
            # nothing she could see had changed.
            is_open=(row.get("id", 0) in parcels if parcels
                     else row is active[0]),
        )
        if not rich.fits(blocks + [section] + tail):
            break
        blocks.append(section)
        shown += 1

    if not shown and active:
        # The budget must never eat the whole screen: a list of orders with no
        # orders on it is not a smaller screen, it is a broken one. One section
        # always goes out, even if that puts the message over — better a
        # refusal we fall back from than a screen that says nothing.
        blocks.append(rich.details(
            _order_summary(active[0], t),
            _order_details(active[0], t, parcel=parcels.get(active[0].get("id", 0))),
            is_open=True))
        shown = 1

    if shown < len(active):
        tail[0] = rich.para(t.MSG_ORDERS_PAGE.format(
            first=1, last=shown, total=len(active)))

    # The tail, minus the reservation for a page line that was not needed.
    blocks += tail[1:] if shown >= len(active) else tail
    return blocks
