"""Both inline lists — the panel above the input field.

The *screens* show the top five products and three orders a page, which is the
right shape for a screen: it fits a phone and needs no search box. This is the
other half of the same question. The panel opens over the keyboard, holds
everything — every product ever bought, every order still cached — with a photo
beside each row, and filters as the customer types.

Two lists, one bot, so the query says which. Telegram gives inline mode a
single entry point, and the button that opens it inserts a word after the bot's
username: with it, orders; without it, favourites. The word is localised and
matched in every language it can be rendered in, because a button sent months
ago outlives a language change.

Two things about inline mode decide most of the code below.

It works in *every* chat the customer is in, not only in the chat with this
bot. What it lists here is a purchase history, so anywhere but the private chat
with the bot the answer is a button and no data.

And picking a result sends a message — from the customer, into whatever chat
they are in. That is the mechanism and it cannot be turned off, so the message
is made worth having: what was picked, and the buttons that do something about
it — buy it, wait for it, ask about it, order that whole basket again. It is
also why this module alone breaks the "one live message" rule the rest of the
bot keeps (bot/screen.py); nothing here can be edited in place, because it is
the customer's own message.
"""
from __future__ import annotations

import hashlib
from html import escape
from urllib.parse import urlparse

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import (CallbackQuery, InlineKeyboardButton,
                           InlineKeyboardMarkup, InlineQuery,
                           InlineQueryResultArticle, InlineQueryResultsButton,
                           InputTextMessageContent)
from loguru import logger

from core import texts
from core.config import AppConfig
from core.domain.offer import Offer
from core.i18n import Texts, variants
from core.repos.catalogue import get_offers
from core.repos.orders import get_cached_orders
from core.repos.stock import (add_stock_subscription, get_stock_levels,
                              get_subscribed_skus, remove_stock_subscription)
from core.repos.users import get_user_phone
from bot.analytics import track
from bot.callbacks import DiscountAction, StockAction
from bot.handlers.common import FAVOURITES_DEEP_LINK, ORDERS_DEEP_LINK
from bot.handlers.orders import (INLINE_LIMIT, favourite_products,
                                 format_cached_order, order_products)
from bot.keyboards import STYLE_CART, STYLE_UNDO, cart_url, product_url

router = Router()

# Telegram accepts fifty results in one answer. Ranking more favourites than
# that would only produce rows nobody scrolls to — and typing filters the list,
# which is the way past fifty. Defined in bot/handlers/orders.py, which ranks
# them, and which a card's discount button asks the same question of.
_MAX_RESULTS = INLINE_LIMIT

# The panel has room for more of a product name than a button does: the row is
# as wide as the screen and the price sits on its own line underneath.
_TITLE_LEN = 60

# Shopify's CDN resizes on request. The panel draws a thumbnail at about fifty
# points, so fetching the 600-pixel product shot for it is bytes nobody sees.
_THUMB_WIDTH = 200

# Taps in the panel are counted apart from the ones on the favourites screen —
# both open the shop, and the shop's analytics is the only place a url button
# can be counted at all.
_CAMPAIGN = "favourites_inline"
# The same, for a basket ordered again out of the orders list. Its own campaign
# because it answers a different question in the shop's analytics: not "does
# the list sell", but "do people repeat whole orders".
_ORDERS_CAMPAIGN = "orders_inline"

# Payload for the button above the list. Telegram sends it to /start, which
# reads it and draws the favourites screen — the same screen the key below the
# input field opened before it became the way into this list. Imported rather
# than spelled again: the writer and the reader of a deep link that agree by
# coincidence stop agreeing the first time one of them is edited.
_START_PARAM = FAVOURITES_DEEP_LINK

# What the button on the orders screen inserts, in every language it can be
# rendered in. Matched rather than compared: the word on a button sent months
# ago is the word of the language the customer had then.
_ORDER_WORDS = {word.casefold() for word in variants("MSG_INLINE_ORDERS_PREFIX")}

# How much of a product name goes on the second line of an order's row, where
# three or four of them are listed side by side.
_ITEM_LEN = 22


@router.inline_query()
async def inline_list(query: InlineQuery, t: Texts, config: AppConfig) -> None:
    """Answer the panel with this customer's own products, or their orders."""
    chat_id = query.from_user.id

    # "sender" is Telegram's word for the private chat with this bot. A group,
    # a channel, somebody else's chat — or a client that does not say which —
    # gets a button back and nothing else. Fail closed: this is a purchase
    # history, and the panel is opened in front of whoever is in the room.
    if query.chat_type != "sender":
        await _button_only(query, t.MSG_INLINE_NOT_HERE, _START_PARAM)
        return

    if not await get_user_phone(chat_id):
        await _button_only(query, t.MSG_INLINE_NEED_PHONE, _START_PARAM)
        return

    orders, needle = _route(query.query)
    if orders:
        results, empty, screen = await _order_results(chat_id, needle, t, config)
        param, event = ORDERS_DEEP_LINK, "orders_inline_opened"
    else:
        results, empty, screen = await _favourite_results(chat_id, needle, t, config)
        param, event = _START_PARAM, "favourites_inline_opened"

    # Counted when the panel opens, not on every keystroke: each character
    # typed arrives here as its own query, and the question worth answering is
    # how many people open a list at all.
    if not needle:
        track(chat_id, event, found=len(results))

    if not results:
        await _button_only(query, empty, param)
        return

    # Personal and uncached: the list is one customer's own, and Telegram must
    # not serve it to the next person who types the same query.
    #
    # The button above it leads to the screen this list came from. «⭐ Улюблені»
    # and «📦 Замовлення» both open a list now, so the screens — and with them
    # the discount request, the subscription, the paging — would otherwise have
    # nothing left pointing at them.
    await query.answer(
        results,
        cache_time=0,
        is_personal=True,
        button=InlineQueryResultsButton(text=screen, start_parameter=param),
    )


def _route(raw: str) -> tuple[bool, str]:
    """Which list was asked for, and what was typed after the asking.

    The first word decides, and only if it is the orders word in one of the
    languages it can be rendered in. Anything else is a product search, which
    is what an empty query is too — favourites is the list that opens when
    nothing says otherwise.
    """
    text = raw.strip()
    head, _, rest = text.partition(" ")
    if head.casefold() in _ORDER_WORDS:
        return True, rest.strip().casefold()
    return False, text.casefold()


async def _favourite_results(chat_id: int, needle: str, t: Texts,
                             config: AppConfig) -> tuple[list, str, str]:
    """Every product this customer has bought, best-loved first.

    Read from the cache and never refreshed from the CRM: an inline answer has
    seconds, every keystroke arrives as its own query, and the screen the
    button sits on is what fills a cold cache.
    """
    ranked = favourite_products(await get_cached_orders(chat_id),
                                limit=_MAX_RESULTS)
    favourites = [item for item in ranked
                  if not needle or needle in str(item["name"]).casefold()]

    offers = await get_offers(str(item.get("sku") or "") for item in favourites)
    # The CRM's unit count, for the products the storefront lists no offer for
    # — about a fifth of the catalogue, samples and sets. Without it those
    # would be missing from a list that promises everything they have bought.
    levels = await get_stock_levels()
    # What they are already waiting for, so a card does not offer again what
    # the customer has already asked for.
    subscribed = await get_subscribed_skus(chat_id)
    results = [
        _result(item, offers.get(str(item.get("sku") or "")), t,
                config.website_url,
                waiting=str(item.get("sku") or "") in subscribed,
                out_of_stock=_is_out_of_stock(item, levels))
        for item in favourites
    ]
    return results, _nothing_to_show(ranked, needle, t), t.MSG_INLINE_SCREEN


def _nothing_to_show(ranked: list[dict], needle: str, t: Texts) -> str:
    """Which kind of nothing this is: never bought anything, or typed something
    that matches nothing they have bought.

    There is no third kind any more. Every favourite is a row now, whether or
    not the shop has an offer for it — a customer whose whole history is
    samples and sets used to open this and be told they had never ordered.
    """
    return t.MSG_INLINE_NOTHING_FOUND if needle and ranked else t.MSG_INLINE_EMPTY


async def _order_results(chat_id: int, needle: str, t: Texts,
                         config: AppConfig) -> tuple[list, str, str]:
    """Every cached order, newest first, as rows that can be searched.

    What the orders screen cannot be: it pages three at a time, and finding
    "the one with that cream in it" there means reading. Here the typing does
    it — the needle is matched against the order's number and against every
    product in it, so a product name finds every order that ever held it.
    """
    cached = (await get_cached_orders(chat_id))[:_MAX_RESULTS]
    orders = [row for row in cached if _order_matches(row, needle)]

    # One lookup for every product on the page: the pictures come from it, and
    # so do the variant ids the "order this again" basket is addressed to.
    offers = await get_offers(
        str(product.get("sku") or "")
        for row in orders for product in order_products(row)
    )
    results = [_order_result(row, offers, t, config.website_url) for row in orders]
    empty = t.MSG_INLINE_NOTHING_FOUND if needle and cached else t.MSG_INLINE_ORDERS_EMPTY
    return results, empty, t.MSG_INLINE_ORDERS_SCREEN


def _order_matches(row: dict, needle: str) -> bool:
    """Whether what was typed picks this order out.

    Matched against what a customer would type: the order's number, or the name
    of something that was in it.
    """
    if not needle:
        return True
    if needle in str(row.get("order_name") or "").casefold():
        return True
    return any(needle in str(product.get("name") or "").casefold()
               for product in order_products(row))


def _order_result(row: dict, offers: dict[str, Offer], t: Texts,
                  website_url: str) -> InlineQueryResultArticle:
    """One order as a row, and as the card picking it sends.

    The card is the same block the orders screen draws, expanded and without
    its number — one formatter, so the two cannot come to disagree about what
    an order looks like.
    """
    products = order_products(row)
    date = texts.short_date(str(row.get("ordered_at") or ""))
    detail = t.MSG_INLINE_ORDER_DETAIL.format(
        status=t.status(str(row.get("status_name") or "")) or "-",
        total=texts.price_label(row.get("grand_total", 0)),
        currency=t.currency(str(row.get("currency") or "грн")),
    )
    items = ", ".join(texts.product_label(str(product.get("name") or ""), _ITEM_LEN)
                      for product in products) or "-"

    return InlineQueryResultArticle(
        # The cache row id, which is what the screen's expand buttons carry too.
        id=f"o{row.get('id', 0)}",
        title=f"{t.order_source_label(row)} · {date}",
        description=f"{detail}\n{items}",
        # The first product's photo. An order has no picture of its own, and
        # this is the one a customer recognises the order by.
        thumbnail_url=_first_photo(products, offers),
        input_message_content=InputTextMessageContent(
            message_text=format_cached_order(row, t, number=0, expanded=True),
            parse_mode="HTML",
        ),
        reply_markup=_order_kb(row, products, offers, t, website_url),
    )


def _first_photo(products: list[dict], offers: dict[str, Offer]) -> str | None:
    """The picture of the first product in the order that has one."""
    for product in products:
        offer = offers.get(str(product.get("sku") or ""))
        if offer is not None and offer.image_url:
            return _thumbnail(offer.image_url)
    return None


def _order_kb(row: dict, products: list[dict], offers: dict[str, Offer],
              t: Texts, website_url: str) -> InlineKeyboardMarkup | None:
    """Order the same basket again, and see where the parcel is.

    The basket is built from what the shop still sells, and the button says so
    when that is less than the order held: repeating four items out of five
    without a word would be a quiet substitution of a different order.

    Deduplicated by variant, because a permalink line is `{variant}:1` and the
    same product twice in one order would otherwise be two lines addressed to
    the same variant.
    """
    variant_ids = list(dict.fromkeys(
        offer.variant_id for product in products
        if (offer := offers.get(str(product.get("sku") or ""))) is not None
        and offer.available
    ))
    rows: list[list[InlineKeyboardButton]] = []
    if variant_ids:
        label = (t.BTN_REORDER if len(variant_ids) >= len(products)
                 else t.BTN_REORDER_PARTIAL.format(available=len(variant_ids),
                                                   total=len(products)))
        rows.append([InlineKeyboardButton(
            text=label,
            url=cart_url(website_url, variant_ids, t.lang, _ORDERS_CAMPAIGN),
            style=STYLE_CART,
        )])

    tracking = str(row.get("tracking_code") or "")
    if tracking:
        rows.append([InlineKeyboardButton(text=t.BTN_TRACK_PARCEL,
                                          url=texts.tracking_url(tracking))])
    return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None


def _is_out_of_stock(item: dict, levels: dict[str, int]) -> bool:
    """True only where the CRM says there is none free to sell.

    An unknown sku is never reported as missing: rows cached before skus were
    stored have none, and claiming "out of stock" for something we cannot look
    up would be worse than staying quiet. Same rule as the favourites screen,
    which is the point — the two must not disagree about one product.
    """
    sku = str(item.get("sku") or "")
    return bool(sku) and sku in levels and levels[sku] <= 0


async def _button_only(query: InlineQuery, text: str, param: str) -> None:
    """Answer with a button above an empty list, and no data at all.

    A query that goes unanswered leaves the panel spinning until it times out,
    so every path answers. The button opens the chat with the bot, which is
    where each of these situations is actually resolved.
    """
    await query.answer(
        [],
        cache_time=0,
        is_personal=True,
        button=InlineQueryResultsButton(text=text, start_parameter=param),
    )


def _result(item: dict, offer: Offer | None, t: Texts, website_url: str,
            waiting: bool = False,
            out_of_stock: bool = False) -> InlineQueryResultArticle:
    """One product as a row in the panel, and as the card picking it sends.

    Every favourite becomes a row, including the fifth or so of the catalogue
    the storefront publishes no offer for — samples, sets. Those have no price
    and no picture to show, and their card can only offer to tell the customer
    when the thing is back; what they must not be is missing from a list that
    says it holds everything this person has bought.
    """
    sku = str(item.get("sku") or "")
    name = texts.product_label(item["name"], _TITLE_LEN)
    history = t.MSG_FAVOURITE_LINE.format(
        orders=item["orders"], qty=item["qty"], date=texts.short_date(item["last"])
    )

    if offer is not None:
        detail = (t.MSG_INLINE_IN_STOCK if offer.available
                  else t.MSG_INLINE_OUT_OF_STOCK).format(
                      price=texts.price_label(offer.price))
    else:
        # No price to put on the line: the shop does not list this one. What
        # can still be said is whether the CRM has any of it.
        detail = t.MSG_FAVOURITE_OUT_OF_STOCK if out_of_stock else ""

    return InlineQueryResultArticle(
        # The sku, so the same product is the same result between queries.
        # Order lines cached before skus were stored have none, and those are
        # grouped by name — hence the fallback, which only has to be unique
        # within one answer.
        id=sku or _fallback_id(item["name"]),
        title=name,
        # Two lines, as the panel draws them: today's price and availability
        # above, and how often this was ordered below.
        description=f"{detail}\n{history}" if detail else history,
        thumbnail_url=_thumbnail(offer.image_url) if offer is not None else None,
        input_message_content=InputTextMessageContent(
            # Escaped like every other HTML message the bot builds: product
            # names here carry '&' by the thousand.
            message_text=t.MSG_INLINE_CARD.format(name=escape(name),
                                                  detail=detail or history),
            parse_mode="HTML",
        ),
        reply_markup=_card_kb(sku, offer, t, website_url, waiting, out_of_stock),
    )


def _card_kb(sku: str, offer: Offer | None, t: Texts, website_url: str,
             waiting: bool, out_of_stock: bool) -> InlineKeyboardMarkup | None:
    """What sits under the card: buy it, or wait for it.

    A card is a message the customer sent through inline mode, so a callback
    from these buttons arrives with an inline_message_id and no message at all
    — which is why the subscription has its own handler below rather than the
    one the favourites screen uses.

    Sold out, the card offers both things there are to do about that: be told
    when it is back, and look at it on the shop, where the shop says in its own
    words what happened to it. A product the shop lists no offer for has only
    the first of those, and one that nothing can be said about — no offer, no
    stock figure — has neither. All of them can ask for a discount, which is
    the one thing that is true of a product whatever the shop knows about it.
    """
    rows: list[list[InlineKeyboardButton]] = []

    if offer is not None and offer.available:
        rows.append([InlineKeyboardButton(
            text=t.BTN_BUY,
            url=cart_url(website_url, [offer.variant_id], t.lang, _CAMPAIGN),
            style=STYLE_CART,
        )])
    else:
        if sku and (offer is not None or out_of_stock):
            rows.append([InlineKeyboardButton(
                text=t.BTN_WAITING_CARD if waiting else t.BTN_NOTIFY_CARD,
                callback_data=StockAction(
                    action="unsub" if waiting else "sub", sku=sku).pack(),
                style=STYLE_UNDO if waiting else None,
            )])
        if offer is not None:
            rows.append([InlineKeyboardButton(
                text=t.BTN_OPEN_PRODUCT,
                url=product_url(website_url, offer.handle, t.lang, _CAMPAIGN),
            )])

    # Last, under whatever else this product allows. It carries the sku, so the
    # manager is asked about the product the customer is looking at rather than
    # about their top five — which is what the same button means on the screen,
    # where it sits under the whole list.
    rows.append([InlineKeyboardButton(
        text=t.BTN_WANT_DISCOUNT_CARD,
        callback_data=DiscountAction(action="ask", sku=sku).pack(),
    )])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(StockAction.filter(), F.inline_message_id)
async def toggle_stock_from_card(
    callback: CallbackQuery,
    callback_data: StockAction,
    config: AppConfig,
    t: Texts,
) -> None:
    """🔔 on a card sent from the list — the same subscription the screen sets.

    Separate from the screen's handler because there is no message to redraw:
    a card belongs to no chat this bot can address, only to an
    inline_message_id, and the screen's version reaches for callback.message.

    The product is looked up in this customer's own history, so a forged sku
    subscribes to nothing rather than to somebody else's product — the same
    guard the screen's handler keeps, for the same reason.
    """
    chat_id = callback.from_user.id
    sku = callback_data.sku
    name = next(
        (item["name"] for item in favourite_products(
            await get_cached_orders(chat_id), limit=_MAX_RESULTS)
         if str(item.get("sku") or "") == sku),
        "",
    )
    if not name:
        await callback.answer()
        return

    waiting = callback_data.action == "sub"
    if waiting:
        await add_stock_subscription(chat_id, sku, name)
        track(chat_id, "stock_subscribed", source="inline")
        await callback.answer(t.MSG_SUBSCRIBED, show_alert=True)
    else:
        await remove_stock_subscription(chat_id, sku)
        track(chat_id, "stock_unsubscribed", source="inline")
        await callback.answer(t.MSG_UNSUBSCRIBED, show_alert=True)

    # The button has to stop offering what was just done. Only the keyboard
    # changes — the card's text is the product and its price, and neither moved.
    offer = (await get_offers([sku])).get(sku)
    try:
        await callback.bot.edit_message_reply_markup(
            inline_message_id=callback.inline_message_id,
            reply_markup=_card_kb(sku, offer, t, config.website_url, waiting,
                                  out_of_stock=True),
        )
    except TelegramBadRequest as exc:
        # A card older than Telegram's edit window, or already showing this.
        # The subscription is stored either way, which is what was asked for.
        logger.debug("Could not redraw an inline card: {}", exc)


def _fallback_id(name: str) -> str:
    """An id for a product with no sku, and the same one every time.

    Order lines cached before skus were stored have none, and favourites groups
    those by name. Hashed rather than built from the name itself because the id
    is capped at 64 bytes and a product name here runs to 147; digested rather
    than hash() because that one is salted per process, and an id that changes
    when the bot restarts is an id nothing can be matched against later.
    """
    return hashlib.sha1(name.encode("utf-8")).hexdigest()[:16]


def _thumbnail(image_url: str) -> str | None:
    """The product photo at thumbnail size, or None for a product without one.

    None is a row with no picture beside it, which is what a product the feed
    has no photo for should look like — not a reason to leave the product out.
    """
    if not image_url:
        return None
    joiner = "&" if urlparse(image_url).query else "?"
    return f"{image_url}{joiner}width={_THUMB_WIDTH}"
