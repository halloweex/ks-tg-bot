"""The orders list: the history as rows, and one tap to order it again.

The screen pages three orders at a time and cannot be searched, so finding "the
one with that cream in it" there means reading. These tests are mostly about
the two things this list adds instead — typing finds an order by what was in
it, and picking one offers the same basket back.

The InlineQuery is a stub for the same reason as in test_favourites_inline: the
real one answers through a bound Bot, and what is under test is what the
handler decides to say.
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest

from bot.handlers.common import ORDERS_DEEP_LINK
from bot.handlers.inline import inline_list
from core.domain.offer import Offer
from core.i18n import Texts
from core.repos import base as repos_base
from core.repos.catalogue import save_offers
from core.repos.orders import upsert_orders
from core.repos.schema import init_db
from core.repos.users import save_user

CHAT = 9090
SHOP = "https://koreanstory.com.ua"
T = Texts("uk")


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(repos_base, "DB_PATH", str(tmp_path / "bot_data.db"))
    asyncio.run(init_db())


@pytest.fixture(autouse=True)
def no_tracking(monkeypatch):
    events = []
    monkeypatch.setattr("bot.handlers.inline.track",
                        lambda chat_id, event, **meta: events.append((event, meta)))
    return events


class _Query:
    """As much of an InlineQuery as the handler touches."""

    def __init__(self, text: str = "", *, chat_type: str = "sender") -> None:
        self.query = text
        self.chat_type = chat_type
        self.from_user = SimpleNamespace(id=CHAT)
        self.results = None
        self.kwargs = None

    async def answer(self, results, **kwargs):
        self.results = results
        self.kwargs = kwargs


def _order(*products, order_id: int = 1, at: str = "2026-08-01T10:00:00",
           name: str = "", tracking: str = "", status: str = "delivered",
           total: float = 1465.0) -> dict:
    return {
        "chat_id": CHAT, "source": "keycrm", "source_order_id": str(order_id),
        "external_id": str(order_id), "order_name": name, "status_name": status,
        "status_group_id": 1, "grand_total": total, "currency": "грн",
        "ordered_at": at,
        "products_json": json.dumps([
            {"name": f"Product {sku}", "qty": 1, "sku": sku} for sku in products
        ]),
        "buyer_name": "", "payment_status": "", "tracking_code": tracking,
        "shipping_status": "", "delivery_city": "", "receive_point": "",
        "recipient_name": "",
    }


def _offer(sku, *, variant=111, available=True, image="") -> Offer:
    return Offer(sku=sku, variant_id=variant, handle="h", title=f"Product {sku}",
                 price="680.00", available=available, image_url=image)


def _config():
    return SimpleNamespace(website_url=SHOP, support_chat_id=-1)


def _ask(text: str = "замовлення ", lang: str = "uk") -> _Query:
    query = _Query(text)
    asyncio.run(inline_list(query, Texts(lang), _config()))
    return query


def _customer(*orders, offers=None):
    asyncio.run(save_user(CHAT, "+380670000000"))
    asyncio.run(upsert_orders(CHAT, list(orders)))
    if offers:
        asyncio.run(save_offers(offers))


def _buttons(row):
    return [b for line in row.reply_markup.inline_keyboard for b in line]


# --- which list answers -----------------------------------------------------

def test_the_word_after_the_username_chooses_the_list(db):
    """One inline entry point, two lists. The button on the orders screen is
    what inserts the word; without it the panel is the favourites one."""
    _customer(_order("1"), offers={"1": _offer("1")})
    assert _ask("замовлення ").results[0].id.startswith("o")
    assert _ask("").results[0].id == "1"


def test_the_word_is_matched_in_every_language_it_can_be_written_in(db):
    """A button sent months ago carries the word of the language the customer
    had then, and languages change."""
    _customer(_order("1"), offers={"1": _offer("1")})
    assert _ask("orders", lang="uk").results[0].id.startswith("o")
    assert _ask("замовлення", lang="en").results[0].id.startswith("o")


def test_a_customer_with_no_orders_is_told_so(db):
    _customer()
    query = _ask()
    assert query.results == []
    assert query.kwargs["button"].text == T.MSG_INLINE_ORDERS_EMPTY


def test_the_bar_above_the_list_leads_to_the_orders_screen(db):
    """Paging and the expand buttons live there, and nothing else points at
    that screen once «📦 Замовлення» opens this list."""
    _customer(_order("1"), offers={"1": _offer("1")})
    query = _ask()
    assert query.kwargs["button"].start_parameter == ORDERS_DEEP_LINK
    assert query.kwargs["button"].text == T.MSG_INLINE_ORDERS_SCREEN


# --- what a row says --------------------------------------------------------

def test_a_row_names_the_order_and_what_was_in_it(db):
    _customer(_order("1", "2", name="#19966", at="2026-06-15T10:00:00"),
              offers={"1": _offer("1"), "2": _offer("2")})
    row = _ask().results[0]
    assert row.title == "🌐 Сайт #19966 · 15.06.2026"
    status, items = row.description.splitlines()
    # The thin space is price_label's, the same one the buy buttons use.
    assert status == "Доставлено · 1\u2009465 грн"
    assert items == "Product 1, Product 2"


def test_a_row_wears_the_photo_of_what_is_in_the_order(db):
    """An order has no picture of its own, and this is the one a customer
    recognises it by."""
    _customer(_order("1", "2"),
              offers={"1": _offer("1"), "2": _offer("2", image="https://cdn/x.jpg")})
    row = _ask().results[0]
    assert row.thumbnail_url.startswith("https://cdn/x.jpg")


def test_the_newest_order_comes_first(db):
    _customer(_order("1", order_id=1, at="2026-06-01T10:00:00"),
              _order("2", order_id=2, at="2026-08-01T10:00:00"),
              offers={"1": _offer("1"), "2": _offer("2")})
    assert [row.title.split(" · ")[1] for row in _ask().results] == [
        "01.08.2026", "01.06.2026"]


# --- typing finds an order --------------------------------------------------

def test_typing_a_product_finds_every_order_that_held_it(db):
    """The thing the screen cannot do at all: it pages three at a time, and
    the product names are inside the blocks."""
    _customer(_order("1", "2", order_id=1, at="2026-06-01T10:00:00"),
              _order("3", order_id=2, at="2026-07-01T10:00:00"),
              _order("2", order_id=3, at="2026-08-01T10:00:00"),
              offers={s: _offer(s) for s in ("1", "2", "3")})
    found = _ask("замовлення product 2").results
    assert [row.id for row in found] == ["o3", "o1"]


def test_typing_an_order_number_finds_that_order(db):
    _customer(_order("1", order_id=1, name="#19966"),
              _order("2", order_id=2, name="#20100", at="2026-08-02T10:00:00"),
              offers={"1": _offer("1"), "2": _offer("2")})
    assert [row.id for row in _ask("замовлення 19966").results] == ["o1"]


def test_a_query_matching_nothing_says_so(db):
    _customer(_order("1"), offers={"1": _offer("1")})
    query = _ask("замовлення тонер")
    assert query.results == []
    assert query.kwargs["button"].text == T.MSG_INLINE_NOTHING_FOUND


# --- what picking one does --------------------------------------------------

def test_the_card_is_the_same_block_the_screen_draws(db):
    """One formatter, so the two cannot come to disagree about what an order
    looks like — without the number, which belongs to a list this card is no
    longer part of."""
    _customer(_order("1", "2", name="#19966"),
              offers={"1": _offer("1"), "2": _offer("2")})
    card = _ask().results[0].input_message_content.message_text
    assert card.startswith("<b>🌐 Сайт #19966</b>")
    assert "• Product 1 ×1" in card and "• Product 2 ×1" in card


def test_picking_an_order_offers_the_same_basket_again(db):
    _customer(_order("1", "2"),
              offers={"1": _offer("1", variant=11), "2": _offer("2", variant=22)})
    button = _buttons(_ask().results[0])[0]
    assert button.text == T.BTN_REORDER
    assert urlparse(button.url).path == "/cart/11:1,22:1"
    tags = parse_qs(urlparse(button.url).query)
    assert tags["utm_campaign"] == ["orders_inline"]


def test_a_basket_says_when_it_can_only_be_half_repeated(db):
    """Repeating four items out of five without a word would be a quiet
    substitution of a different order."""
    _customer(_order("1", "2", "3"),
              offers={"1": _offer("1", variant=11),
                      "2": _offer("2", variant=22, available=False),
                      "3": _offer("3", variant=33)})
    button = _buttons(_ask().results[0])[0]
    assert button.text == T.BTN_REORDER_PARTIAL.format(available=2, total=3)
    assert urlparse(button.url).path == "/cart/11:1,33:1"


def test_the_same_product_twice_is_one_line_in_the_permalink(db):
    """A permalink line is `{variant}:1`, and two lines addressed to the same
    variant is a basket nobody asked for."""
    _customer(_order("1", "1"), offers={"1": _offer("1", variant=11)})
    button = _buttons(_ask().results[0])[0]
    assert urlparse(button.url).path == "/cart/11:1"


def test_an_order_of_nothing_the_shop_still_sells_offers_no_basket(db):
    """A cart link to variants that cannot be bought ends at a checkout that
    refuses it."""
    _customer(_order("1"), offers={"1": _offer("1", available=False)})
    assert _ask().results[0].reply_markup is None


def test_a_parcel_on_its_way_can_be_tracked_from_the_card(db):
    _customer(_order("1", tracking="59000123456"), offers={"1": _offer("1")})
    track_button = _buttons(_ask().results[0])[-1]
    assert track_button.text == T.BTN_TRACK_PARCEL
    assert "59000123456" in track_button.url


# --- counted the same way as the other list ---------------------------------

def test_opening_the_list_is_counted_once_and_keystrokes_are_not(db, no_tracking):
    _customer(_order("1"), offers={"1": _offer("1")})
    _ask("замовлення ")
    _ask("замовлення p")
    _ask("замовлення pr")
    assert [event for event, _meta in no_tracking] == ["orders_inline_opened"]
