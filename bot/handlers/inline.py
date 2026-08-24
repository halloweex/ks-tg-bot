"""Favourites as Telegram's own inline list — the panel above the input field.

The favourites *screen* shows the top five products with a buy button each, and
that is the right shape for a screen: it fits a phone without scrolling and
needs no search box. This is the other half of the same question. The panel
opens over the keyboard, lists everything the customer has ever bought with a
photo beside each row, and filters as they type — which is what a customer with
thirty orders behind them actually needs, and what five buttons cannot be.

Two things about inline mode decide most of the code below.

It works in *every* chat the customer is in, not only in the chat with this
bot. What it lists here is a purchase history, so anywhere but the private chat
with the bot the answer is a button and no data.

And picking a result sends a message — from the customer, into whatever chat
they are in. That is the mechanism and it cannot be turned off, so the message
is made worth having: the product's name, today's price, and the one button
that does something about it. It is also why this screen alone breaks the "one
live message" rule the rest of the bot keeps (bot/screen.py); nothing here can
be edited in place, because it is the customer's own message.
"""
from __future__ import annotations

from html import escape
from urllib.parse import urlparse

from aiogram import Router
from aiogram.types import (InlineKeyboardButton, InlineKeyboardMarkup,
                           InlineQuery, InlineQueryResultArticle,
                           InlineQueryResultsButton, InputTextMessageContent)

from core import texts
from core.config import AppConfig
from core.domain.offer import Offer
from core.i18n import Texts
from core.repos.catalogue import get_offers
from core.repos.orders import get_cached_orders
from core.repos.users import get_user_phone
from bot.analytics import track
from bot.handlers.orders import favourite_products
from bot.keyboards import cart_url, product_url

router = Router()

# Telegram accepts fifty results in one answer. Ranking more favourites than
# that would only produce rows nobody scrolls to — and typing filters the list,
# which is the way past fifty.
_MAX_RESULTS = 50

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

# Payload for the "open the bot" button above an empty panel. Telegram sends it
# to /start, which ignores it: the point is landing in the chat, not the value.
_START_PARAM = "favourites"


@router.inline_query()
async def favourites_inline(query: InlineQuery, t: Texts, config: AppConfig) -> None:
    """Answer the inline panel with this customer's own products."""
    chat_id = query.from_user.id

    # "sender" is Telegram's word for the private chat with this bot. A group,
    # a channel, somebody else's chat — or a client that does not say which —
    # gets a button back and nothing else. Fail closed: this is a purchase
    # history, and the panel is opened in front of whoever is in the room.
    if query.chat_type != "sender":
        await _button_only(query, t.MSG_INLINE_NOT_HERE)
        return

    if not await get_user_phone(chat_id):
        await _button_only(query, t.MSG_INLINE_NEED_PHONE)
        return

    # Read from the cache and never refresh from the CRM: an inline answer has
    # seconds, every keystroke arrives as its own query, and the screen this
    # button sits on is what fills a cold cache.
    favourites = favourite_products(await get_cached_orders(chat_id),
                                    limit=_MAX_RESULTS)
    needle = query.query.strip().casefold()
    if needle:
        favourites = [item for item in favourites
                      if needle in str(item["name"]).casefold()]

    offers = await get_offers(str(item.get("sku") or "") for item in favourites)
    results = [_result(item, offers[str(item["sku"])], t, config.website_url)
               for item in favourites if str(item.get("sku") or "") in offers]

    # Counted when the panel opens, not on every keystroke: each character
    # typed arrives here as its own query, and the question worth answering is
    # how many people open the list at all.
    if not needle:
        track(chat_id, "favourites_inline_opened", found=len(results))

    if not results:
        await _button_only(query, t.MSG_INLINE_EMPTY)
        return
    # Personal and uncached: the list is one customer's own, and Telegram must
    # not serve it to the next person who types the same query.
    await query.answer(results, cache_time=0, is_personal=True)


async def _button_only(query: InlineQuery, text: str) -> None:
    """Answer with a button above an empty list, and no data at all.

    A query that goes unanswered leaves the panel spinning until it times out,
    so every path answers. The button opens the chat with the bot, which is
    where each of these three situations is actually resolved.
    """
    await query.answer(
        [],
        cache_time=0,
        is_personal=True,
        button=InlineQueryResultsButton(text=text, start_parameter=_START_PARAM),
    )


def _result(item: dict, offer: Offer, t: Texts,
            website_url: str) -> InlineQueryResultArticle:
    """One product as a row in the panel, and as the card picking it sends.

    Only products the storefront has an offer for get this far, which is what
    keeps every row here answerable: a price to show and a page to open. The
    fifth or so of the catalogue the shop lists no offer for — samples, sets —
    is named on the favourites screen instead, in the line that exists for
    exactly the products nothing can be pressed for.
    """
    name = texts.product_label(item["name"], _TITLE_LEN)
    price = texts.price_label(offer.price)
    history = t.MSG_FAVOURITE_LINE.format(
        orders=item["orders"], qty=item["qty"], date=texts.short_date(item["last"])
    )

    if offer.available:
        detail = t.MSG_INLINE_IN_STOCK.format(price=price)
        button = InlineKeyboardButton(
            text=t.BTN_BUY,
            url=cart_url(website_url, [offer.variant_id], t.lang, _CAMPAIGN),
        )
    else:
        detail = t.MSG_INLINE_OUT_OF_STOCK.format(price=price)
        button = InlineKeyboardButton(
            text=t.BTN_OPEN_PRODUCT,
            url=product_url(website_url, offer.handle, t.lang, _CAMPAIGN),
        )

    return InlineQueryResultArticle(
        # The sku, so the same product is the same result between queries.
        # Unique by construction: favourites are grouped by it, and a product
        # without one never has an offer to be shown by.
        id=offer.sku,
        title=name,
        # Two lines, as the panel draws them: today's price and availability
        # above, and how often this was ordered below.
        description=f"{detail}\n{history}",
        thumbnail_url=_thumbnail(offer.image_url),
        input_message_content=InputTextMessageContent(
            # Escaped like every other HTML message the bot builds: product
            # names here carry '&' by the thousand.
            message_text=t.MSG_INLINE_CARD.format(name=escape(name), detail=detail),
            parse_mode="HTML",
        ),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[button]]),
    )


def _thumbnail(image_url: str) -> str | None:
    """The product photo at thumbnail size, or None for a product without one.

    None is a row with no picture beside it, which is what a product the feed
    has no photo for should look like — not a reason to leave the product out.
    """
    if not image_url:
        return None
    joiner = "&" if urlparse(image_url).query else "?"
    return f"{image_url}{joiner}width={_THUMB_WIDTH}"
