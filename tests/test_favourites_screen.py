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
from core.repos.schema import init_db
from core.repos.stock import add_stock_subscription, save_stock_levels

CHAT = 4242
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


def _view(orders, lang="uk"):
    text, kb, _n = asyncio.run(
        _favourites_view(CHAT, Texts(lang), orders, SHOP)
    )
    buttons = [b for row in kb.inline_keyboard for b in row]
    return re.sub(r"<[^>]+>", "", text), buttons


def _labels(buttons):
    return [b.text for b in buttons]


# --- the buy button --------------------------------------------------------

def test_a_sellable_favourite_gets_a_cart_link_to_its_own_variant(db):
    asyncio.run(save_offers({"1": _offer("1", variant=99)}))
    _text, buttons = _view([_order("1")])
    buy = buttons[0]
    assert urlparse(buy.url).path == "/cart/99:1"
    assert buy.text.startswith("🛒 Product 1")
    assert buy.text.endswith("680 ₴")


def test_a_button_names_its_own_product(db):
    """The numbered buttons this replaced needed a legend under the list saying
    what the numbers meant. Nothing here has to be matched to anything else."""
    asyncio.run(save_offers({"1": _offer("1"), "2": _offer("2", variant=22)}))
    text, buttons = _view([_order("1", "2")])
    assert [b.text.split(" · ")[0] for b in buttons[:2]] == ["🛒 Product 1", "🛒 Product 2"]
    assert "номер" not in text


def test_the_cart_link_is_tagged_so_the_shop_can_count_it(db):
    """Telegram reports nothing when a url button is tapped. The shop's own
    analytics is the only place this purchase can ever be attributed."""
    asyncio.run(save_offers({"1": _offer("1")}))
    _text, buttons = _view([_order("1")])
    tags = parse_qs(urlparse(buttons[0].url).query)
    assert tags["utm_source"] == ["telegram"]
    assert tags["utm_campaign"] == ["favourites"]


def test_the_checkout_opens_in_the_customers_own_language(db):
    asyncio.run(save_offers({"1": _offer("1")}))
    _text, buttons = _view([_order("1")], lang="en")
    assert parse_qs(urlparse(buttons[0].url).query)["locale"] == ["en"]


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
    assert [b.text.split(" · ")[0] for b in buttons] == [
        "🛒 Product 1", "💰 Хочу знижку на ці товари"]


# --- who decides that something is gone ------------------------------------

def test_the_storefront_outranks_the_crm_count(db):
    """A product can have units and be unpublished. The customer would find no
    way to buy it, so the screen must not offer one."""
    asyncio.run(save_offers({"1": _offer("1", available=False)}))
    asyncio.run(save_stock_levels({"1": 50}))
    _text, buttons = _view([_order("1")])
    assert not any(b.text.startswith("🛒 Product") for b in buttons)
    assert "🔔 Повідомити: Product 1" in _labels(buttons)


def test_without_an_offer_the_crm_count_still_answers(db):
    """About a fifth of the catalogue is sold but never listed — samples, sets.
    Those keep the behaviour the screen had before the storefront existed."""
    asyncio.run(save_stock_levels({"1": 0}))
    _text, buttons = _view([_order("1")])
    assert "🔔 Повідомити: Product 1" in _labels(buttons)


def test_where_neither_source_knows_the_screen_says_nothing(db):
    """Older cached order lines carry no sku at all. Claiming 'out of stock' for
    something we simply cannot look up would be worse than staying quiet."""
    text, buttons = _view([_order("1")])
    assert _labels(buttons) == ["💰 Хочу знижку на ці товари"]
    # ...but it is still named, or it would vanish off a screen whose whole job
    # is to list what this person buys.
    assert "Також ви купували: Product 1" in text


def test_a_subscription_already_taken_shows_as_cancellable(db):
    asyncio.run(save_offers({"1": _offer("1", available=False)}))
    asyncio.run(add_stock_subscription(CHAT, "1", "Product 1"))
    _text, buttons = _view([_order("1")])
    assert "✅ Чекаєте: Product 1" in _labels(buttons)


# --- the hints under the list ----------------------------------------------

def test_the_message_is_a_heading_and_an_instruction(db):
    """Everything else moved onto the buttons. The customer's own "⭐ Улюблені"
    is already the line above this one — echoed into the chat by the menu key
    they pressed — so a header restating it would be the second of three lines
    before anything actionable."""
    asyncio.run(save_offers({"1": _offer("1")}))
    text, _buttons = _view([_order("1"), _order("1")])
    assert text.splitlines()[0] == "⭐ Ваші постійні засоби"
    assert "Натисніть, щоб замовити ще раз" in text
    assert "замовлень:" not in text


def test_a_one_off_purchase_is_not_called_a_favourite(db):
    """Calling something bought once "the one you love most" is a claim the data
    does not support — a quarter of customers have only ever bought one thing."""
    asyncio.run(save_offers({"1": _offer("1")}))
    text, _buttons = _view([_order("1")])
    assert text.splitlines()[0] == "🛍 Ви це вже купували"


# --- the way to the rest of the list ---------------------------------------

def test_the_screen_offers_the_inline_panel_once_there_is_more_than_it_shows(db):
    """The screen lists five products. The inline panel lists everything the
    customer has bought, with a photo each and filtering as they type — worth a
    button exactly when there is a sixth product it would show."""
    skus = [str(n) for n in range(1, 7)]
    asyncio.run(save_offers({s: _offer(s) for s in skus}))
    _text, buttons = _view([_order(*skus)])
    panel = next(b for b in buttons if b.text == "🔍 Усе, що ви купували")
    assert panel.switch_inline_query_current_chat == ""


def test_five_products_or_fewer_get_no_button_to_the_panel(db):
    """It would open a list of the same five products, with pictures."""
    skus = [str(n) for n in range(1, 6)]
    asyncio.run(save_offers({s: _offer(s) for s in skus}))
    _text, buttons = _view([_order(*skus)])
    assert not any(b.text == "🔍 Усе, що ви купували" for b in buttons)
