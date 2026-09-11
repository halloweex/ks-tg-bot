"""What the favourites screen offers, and on whose authority.

The screen answers "can I buy this again" from two sources that disagree by
design — the storefront's offer and the CRM's unit count — so most of these are
about which one wins where.
"""
from __future__ import annotations

import asyncio
import json
import re
from urllib.parse import parse_qs, urlparse

import pytest

from bot.handlers.orders import _favourites_view
from core.domain.offer import Offer
from core.i18n import Texts
from core.repos import base as repos_base
from core.repos.catalogue import save_offers
from core.repos import support as support_repo
from core.repos.schema import init_db
from core.repos.stock import add_stock_subscription, save_stock_levels

CHAT = 4242
T = Texts("uk")
SHOP = "https://koreanstory.com.ua"


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(repos_base, "DB_PATH", str(tmp_path / "bot_data.db"))
    asyncio.run(init_db())


def _order(*products) -> dict:
    return {
        "id": 1, "chat_id": CHAT, "status_group_id": 1, "grand_total": 100,
        "ordered_at": "2026-08-01T10:00:00", "status_name": "completed",
        "products_json": json.dumps([
            {"name": f"Product {sku}", "qty": 1, "sku": sku} for sku in products
        ]),
    }


def _offer(sku, *, variant=111, available=True, price="680.00") -> Offer:
    return Offer(sku=sku, variant_id=variant, handle="h", title=f"Product {sku}",
                 price=price, available=available)


def _buttons_in(blocks) -> list:
    """Every button in the blocks, wherever it sits.

    The screen moved its buttons from the slab under the message into the cards
    they belong to, beside each product's own picture. What these tests are
    about did not move: whether the button exists, what it says, where its cart
    link points. So they ask the screen rather than the keyboard, and stop
    caring which container holds the answer."""
    found = []

    def walk(node):
        if getattr(node, "type", None) == "buttons":
            found.extend(node.buttons)
            return
        for name in getattr(type(node), "model_fields", {}):
            value = getattr(node, name, None)
            if isinstance(value, (list, tuple)):
                for item in value:
                    walk(item)
            elif hasattr(value, "model_fields"):
                walk(value)

    for block in blocks or []:
        walk(block)
    return found


def _cards(blocks) -> list[dict]:
    """The screen grouped the way the reader sees it: a picture, a name, a
    price and the button that acts on them, up to the rule that ends the card.

    The plain screen had to put the product's name inside the button, because a
    button was the only place the name and the action could be together. In a
    card the name is above the button, in bold, under the product's own
    picture — so a test asking "does this button name its product" has to ask
    the card instead."""
    cards, current = [], None
    for block in blocks or []:
        kind = getattr(block, "type", None)
        if kind == "divider":
            if current:
                cards.append(current)
            current = None
            continue
        if kind == "photo":
            current = current or {"name": "", "lines": [], "buttons": []}
            current["photo"] = block.photo.media
            continue
        if kind == "paragraph":
            said = block.text
            # A card's name is the one paragraph built as rich text rather than
            # a plain string — it is bold, which is what this helper's docstring
            # means by "the name is above the button, in bold". The screen also
            # carries prose that belongs to no card (the lead under the
            # heading), and starting a card on it invented a product called
            # "Натисни, щоб замовити ще раз 👇".
            is_name = isinstance(said, list)
            if isinstance(said, list):
                said = "".join(getattr(x, "text", str(x)) for x in said)
            if current is None and not is_name:
                continue
            current = current or {"name": "", "lines": [], "buttons": []}
            if not current["name"]:
                current["name"] = said
            else:
                current["lines"].append(said)
            continue
        if kind == "buttons" and current is not None:
            current["buttons"].extend(block.buttons)
    if current:
        cards.append(current)
    return cards


def _view_cards(orders, lang="uk"):
    _text, _kb, blocks, _n = asyncio.run(
        _favourites_view(CHAT, Texts(lang), orders, SHOP))
    return _cards(blocks)


def _view(orders, lang="uk"):
    text, kb, blocks, _n = asyncio.run(
        _favourites_view(CHAT, Texts(lang), orders, SHOP)
    )
    buttons = [b for row in kb.inline_keyboard for b in row]
    buttons += _buttons_in(blocks)
    return re.sub(r"<[^>]+>", "", text), buttons


def _plain(html: str) -> str:
    """The string as the customer sees it, without the markup."""
    return re.sub(r"<[^>]+>", "", html)


def _labels(buttons):
    return [b.text for b in buttons]


def _products(buttons):
    """Everything but the way into the inline panel and the way back to the menu.

    Those two are about the screen rather than about any one product, and the
    tests below are all about one product."""
    return [b for b in buttons
            if b.switch_inline_query_current_chat is None and b.text != T.BTN_MENU]


# --- the buy button --------------------------------------------------------

def test_a_sellable_favourite_gets_a_cart_link_to_its_own_variant(db):
    asyncio.run(save_offers({"1": _offer("1", variant=99)}))
    card = _view_cards([_order("1")])[0]
    assert card["name"] == "Product 1"
    assert "680 ₴" in " ".join(card["lines"])
    assert urlparse(card["buttons"][0].url).path == "/cart/99:1"


def test_every_button_sits_with_the_product_it_acts_on(db):
    """The numbered buttons this replaced needed a legend saying what the
    numbers meant. Nothing here has to be matched to anything else: the name is
    the line above the button, under that product's own picture."""
    asyncio.run(save_offers({"1": _offer("1"), "2": _offer("2", variant=22)}))
    cards = _view_cards([_order("1", "2")])
    assert [c["name"] for c in cards[:2]] == ["Product 1", "Product 2"]
    assert urlparse(cards[0]["buttons"][0].url).path.endswith("111:1")
    assert urlparse(cards[1]["buttons"][0].url).path.endswith("22:1")


def test_the_cart_link_is_tagged_so_the_shop_can_count_it(db):
    """Telegram reports nothing when a url button is tapped. The shop's own
    analytics is the only place this purchase can ever be attributed."""
    asyncio.run(save_offers({"1": _offer("1")}))
    _text, buttons = _view([_order("1")])
    tags = parse_qs(urlparse(_products(buttons)[0].url).query)
    assert tags["utm_source"] == ["telegram"]
    assert tags["utm_campaign"] == ["favourites"]


def test_the_checkout_opens_in_the_customers_own_language(db):
    asyncio.run(save_offers({"1": _offer("1")}))
    card = _view_cards([_order("1")], lang="en")[0]
    assert parse_qs(urlparse(card["buttons"][0].url).query)["locale"] == ["en"]


def test_one_basket_holds_everything_available(db):
    asyncio.run(save_offers({
        "1": _offer("1", variant=11, price="680.00"),
        "2": _offer("2", variant=22, price="785.00"),
        "3": _offer("3", variant=33, available=False),
    }))
    _text, buttons = _view([_order("1", "2", "3")])
    basket = next(b for b in buttons if b.text.startswith("🛒 Усе разом"))
    assert urlparse(basket.url).path == "/cart/11:1,22:1"
    assert basket.text.endswith("1\u2009465 ₴"), basket.text


def test_a_single_available_product_gets_no_order_everything_button(db):
    """It would be the button directly above it, worded at greater length."""
    asyncio.run(save_offers({"1": _offer("1")}))
    _text, buttons = _view([_order("1")])
    assert not any(b.text.startswith(T.BTN_BUY_ALL[:5]) for b in buttons)


# --- what the discount button is allowed to claim ---------------------------

def test_the_ask_claims_a_habit_only_where_there_is_one(db):
    """«Хочу знижку на ці товари» says the list is what she keeps coming back
    for. Bought once each, that is a discount asked for on the strength of a
    habit she does not have yet — the same fact the header is chosen by."""
    asyncio.run(save_offers({"1": _offer("1")}))

    _text, once = _view([_order("1")])
    assert T.BTN_WANT_DISCOUNT_PLAIN in _labels(once)
    assert T.BTN_WANT_DISCOUNT not in _labels(once)

    _text, twice = _view([_order("1"), _order("1")])
    assert T.BTN_WANT_DISCOUNT in _labels(twice)
    assert T.BTN_WANT_DISCOUNT_PLAIN not in _labels(twice)


def test_the_ask_leaves_a_tick_where_the_offer_was(db):
    """The pop-up is gone a second later. The button is what still says, next
    week, that this question was already asked — the same way the notify button
    remembers a subscription."""
    asyncio.run(save_offers({"1": _offer("1")}))

    _text, before = _view([_order("1")])
    assert T.BTN_WANT_DISCOUNT_PLAIN in _labels(before)

    asyncio.run(support_repo.add_discount_request(
        CHAT, "[]", sku="", thread_message_id=55))
    _text, after = _view([_order("1")])
    assert T.BTN_DISCOUNT_ASKED in _labels(after)
    assert T.BTN_WANT_DISCOUNT_PLAIN not in _labels(after)

    # And an answer gives the question back.
    asyncio.run(support_repo.mark_discount_answered(55))
    _text, answered = _view([_order("1")])
    assert T.BTN_WANT_DISCOUNT_PLAIN in _labels(answered)


# --- who decides that something is gone ------------------------------------

def test_the_storefront_outranks_the_crm_count(db):
    """A product can have units and be unpublished. The customer would find no
    way to buy it, so the screen must not offer one."""
    asyncio.run(save_offers({"1": _offer("1", available=False)}))
    asyncio.run(save_stock_levels({"1": 50}))
    card = _view_cards([_order("1")])[0]
    assert not any(b.url for b in card["buttons"]), "nothing to buy it with"
    assert card["buttons"][0].text == T.BTN_NOTIFY_SHORT


def test_without_an_offer_the_crm_count_still_answers(db):
    """About a fifth of the catalogue is sold but never listed — samples, sets.
    Those keep the behaviour the screen had before the storefront existed."""
    asyncio.run(save_stock_levels({"1": 0}))
    card = _view_cards([_order("1")])[0]
    assert card["buttons"][0].text == T.BTN_NOTIFY_SHORT


def test_where_neither_source_knows_the_screen_says_nothing(db):
    """Older cached order lines carry no sku at all. Claiming 'out of stock' for
    something we simply cannot look up would be worse than staying quiet."""
    text, buttons = _view([_order("1")])
    assert _labels(_products(buttons)) == ["💰 Хочу знижку"]
    # ...but it is still named, or it would vanish off a screen whose whole job
    # is to list what this person buys.
    assert _plain(T.MSG_FAVOURITES_ALSO.format(names="Product 1")) in text


def test_a_subscription_already_taken_shows_as_cancellable(db):
    asyncio.run(save_offers({"1": _offer("1", available=False)}))
    asyncio.run(add_stock_subscription(CHAT, "1", "Product 1"))
    card = _view_cards([_order("1")])[0]
    waiting = card["buttons"][0]
    assert waiting.text == T.BTN_NOTIFY_WAITING_SHORT
    assert waiting.style == "danger", (
        "red only where pressing it cancels a promise she asked for")


# --- the hints under the list ----------------------------------------------

def test_the_message_is_a_heading_and_an_instruction(db):
    """Everything else moved onto the buttons. The customer's own "⭐ Улюблені"
    is already the line above this one — echoed into the chat by the menu key
    they pressed — so a header restating it would be the second of three lines
    before anything actionable."""
    asyncio.run(save_offers({"1": _offer("1")}))
    text, _buttons = _view([_order("1"), _order("1")])
    assert text.splitlines()[0] == _plain(T.MSG_FAVOURITES_HEADER).splitlines()[0]
    assert text.splitlines()[1] == _plain(T.MSG_FAVOURITES_HEADER).splitlines()[1]
    assert "замовлень:" not in text


def test_a_one_off_purchase_is_not_called_a_favourite(db):
    """Calling something bought once "the one you love most" is a claim the data
    does not support — a quarter of customers have only ever bought one thing."""
    asyncio.run(save_offers({"1": _offer("1")}))
    text, _buttons = _view([_order("1")])
    assert text.splitlines()[0] == _plain(T.MSG_FAVOURITES_HEADER_ONCE).splitlines()[0]


# --- the way to the rest of the list ---------------------------------------

def test_the_panel_is_the_first_button_on_the_screen(db):
    """«⭐ Улюблені» is a reply-keyboard key: it can send its own text and
    nothing else, and no API writes into the input field. This button is as
    close as the menu key gets to opening the panel itself — one tap, and the
    client puts "@bot " in the field and draws the list over the keyboard."""
    asyncio.run(save_offers({"1": _offer("1")}))
    _text, buttons = _view([_order("1")])
    assert buttons[0].text == T.BTN_FAVOURITES_ALL
    assert buttons[0].switch_inline_query_current_chat == ""


def test_the_panel_is_offered_even_where_nothing_can_be_bought(db):
    """A screen whose products the shop knows nothing about has no buy buttons
    at all, and the panel is then the only thing on it worth pressing. It
    answers for itself when it turns out to have no rows either — see
    test_a_history_the_shop_lists_no_offer_for_is_not_called_empty."""
    _text, buttons = _view([_order("1")])
    assert buttons[0].switch_inline_query_current_chat == ""
