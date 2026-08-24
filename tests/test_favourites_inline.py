"""What the inline panel answers with, and to whom.

The panel is opened over the keyboard and lists what this customer has bought,
which makes two of these tests the important ones: it is answered only in the
chat with the bot, and only for a customer whose number we hold. Everything
else is about a row being worth tapping — a price, a picture, one button.

The InlineQuery is a stub rather than the aiogram model on purpose: the real
one answers through a bound Bot, and what is under test here is what the
handler decides to say, not that aiogram can serialise it.
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest

from bot.handlers.common import FAVOURITES_DEEP_LINK, cmd_start
from bot.handlers.inline import inline_list
from bot.handlers.orders import request_discount
from core.config import AppConfig
from core.domain.offer import Offer
from core.i18n import Texts
from core.repos import base as repos_base
from bot.callbacks import DiscountAction, StockAction
from core.repos.catalogue import save_offers
from core.repos.orders import upsert_orders
from core.repos.schema import init_db
from core.repos.stock import add_stock_subscription, save_stock_levels
from core.repos.users import save_user

CHAT = 7171
SHOP = "https://koreanstory.com.ua"
PHOTO = "https://cdn.example/a.jpg?v=1"
SUPPORT = -100500


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(repos_base, "DB_PATH", str(tmp_path / "bot_data.db"))
    asyncio.run(init_db())


@pytest.fixture(autouse=True)
def no_tracking(monkeypatch):
    """Collects the analytics calls instead of spawning writes into the loop."""
    events = []
    monkeypatch.setattr("bot.handlers.inline.track",
                        lambda chat_id, event, **meta: events.append((event, meta)))
    return events


class _Query:
    """As much of an InlineQuery as the handler touches."""

    def __init__(self, text: str = "", *, chat_type: str = "sender",
                 user_id: int = CHAT) -> None:
        self.query = text
        self.chat_type = chat_type
        self.from_user = SimpleNamespace(id=user_id)
        self.results = None
        self.kwargs = None

    async def answer(self, results, **kwargs):
        self.results = results
        self.kwargs = kwargs


def _labels(row) -> list[str]:
    return [b.text for line in row.reply_markup.inline_keyboard for b in line]


def _order(*products, order_id: int = 1, at: str = "2026-08-01T10:00:00") -> dict:
    return {
        "chat_id": CHAT, "source": "keycrm", "source_order_id": str(order_id),
        "external_id": str(order_id), "order_name": "", "status_name": "delivered",
        "status_group_id": 1, "grand_total": 100.0, "currency": "грн",
        "ordered_at": at,
        "products_json": json.dumps([
            {"name": f"Product {sku}", "qty": 1, "sku": sku} for sku in products
        ]),
        "buyer_name": "", "payment_status": "", "tracking_code": "",
        "shipping_status": "", "delivery_city": "", "receive_point": "",
        "recipient_name": "",
    }


def _offer(sku, *, variant=111, available=True, price="680.00",
           image=PHOTO, handle="h") -> Offer:
    return Offer(sku=sku, variant_id=variant, handle=handle, title=f"Product {sku}",
                 price=price, available=available, image_url=image)


def _config() -> AppConfig:
    return SimpleNamespace(website_url=SHOP, support_chat_id=SUPPORT)


def _ask(query: _Query, lang: str = "uk") -> _Query:
    asyncio.run(inline_list(query, Texts(lang), _config()))
    return query


def _registered_customer(*orders, offers=None):
    asyncio.run(save_user(CHAT, "+380670000000"))
    asyncio.run(upsert_orders(CHAT, list(orders)))
    if offers:
        asyncio.run(save_offers(offers))


# --- who gets an answer at all ---------------------------------------------

def test_the_list_is_answered_only_in_the_chat_with_the_bot(db):
    """Inline mode works in every chat the customer is in, and this list is
    their purchase history. Anywhere else it is a button and no data."""
    _registered_customer(_order("1"), offers={"1": _offer("1")})
    for chat_type in ("group", "supergroup", "channel", "private", None):
        query = _ask(_Query(chat_type=chat_type))
        assert query.results == []
        assert query.kwargs["button"].text == Texts("uk").MSG_INLINE_NOT_HERE


def test_a_customer_who_has_not_shared_a_number_gets_a_button(db):
    """Same rule the orders screen keeps: no verified number, no history."""
    query = _ask(_Query())
    assert query.results == []
    assert query.kwargs["button"].text == Texts("uk").MSG_INLINE_NEED_PHONE


def test_a_customer_with_nothing_bought_yet_gets_a_button(db):
    _registered_customer()
    query = _ask(_Query())
    assert query.results == []
    assert query.kwargs["button"].text == Texts("uk").MSG_INLINE_EMPTY


def test_a_history_the_shop_lists_no_offer_for_is_still_a_list(db):
    """About a fifth of the catalogue is sold but never listed — samples, sets.
    A customer whose whole history is those has ordered plenty, and the list
    that promises everything they bought has to hold them."""
    _registered_customer(_order("1"))
    row = _ask(_Query()).results[0]
    assert row.title == "Product 1"
    assert row.thumbnail_url is None
    assert "замовлень: 1" in row.description


def test_the_answer_is_personal_and_never_cached(db):
    """Telegram must not serve one customer's products to the next person who
    types the same query."""
    _registered_customer(_order("1"), offers={"1": _offer("1")})
    query = _ask(_Query())
    assert (query.kwargs["is_personal"], query.kwargs["cache_time"]) == (True, 0)


# --- what a row says --------------------------------------------------------

def test_a_row_carries_the_product_its_price_and_its_photo(db):
    _registered_customer(_order("1"), offers={"1": _offer("1", image=PHOTO)})
    row = _ask(_Query()).results[0]
    assert row.title == "Product 1"
    assert row.description.startswith("680 ₴")
    assert row.thumbnail_url.startswith(PHOTO)


def test_the_photo_is_asked_for_at_thumbnail_size(db):
    """The panel draws it at about fifty points; the full product shot is bytes
    nobody sees."""
    _registered_customer(_order("1"), offers={"1": _offer("1")})
    row = _ask(_Query()).results[0]
    assert parse_qs(urlparse(row.thumbnail_url).query)["width"] == ["200"]


def test_a_product_with_no_photo_is_still_a_row(db):
    """No picture is a row without a thumbnail, not a product the customer
    stops being able to reorder."""
    _registered_customer(_order("1"), offers={"1": _offer("1", image="")})
    row = _ask(_Query()).results[0]
    assert row.thumbnail_url is None
    assert row.title == "Product 1"


def test_the_second_line_says_how_often_it_was_ordered(db):
    _registered_customer(_order("1", order_id=1, at="2026-06-15T10:00:00"),
                         _order("1", order_id=2, at="2026-08-01T10:00:00"),
                         offers={"1": _offer("1")})
    row = _ask(_Query()).results[0]
    assert row.description.splitlines()[1] == "замовлень: 2 · 2 шт · востаннє 01.08.2026"


def test_a_product_the_shop_has_no_offer_for_is_a_row_without_a_price(db):
    """It has no price and no picture — but it is something this customer buys,
    and the list is of what they buy."""
    _registered_customer(_order("1", "2"), offers={"1": _offer("1")})
    rows = _ask(_Query()).results
    assert sorted(row.id for row in rows) == ["1", "2"]
    plain = next(row for row in rows if row.id == "2")
    assert "₴" not in plain.description
    # Nothing to buy and nothing to wait for, but a discount can be asked about
    # anything — see test_a_card_asks_for_a_discount_on_its_own_product.
    assert _labels(plain) == [Texts("uk").BTN_WANT_DISCOUNT_CARD]


def test_a_product_with_no_offer_but_no_stock_can_still_be_waited_for(db):
    """Where the storefront says nothing, the CRM's count answers — the same
    fallback the favourites screen uses, so the two cannot disagree."""
    _registered_customer(_order("1"))
    asyncio.run(save_stock_levels({"1": 0}))
    row = _ask(_Query()).results[0]
    notify = row.reply_markup.inline_keyboard[0][0]
    assert notify.text == Texts("uk").BTN_NOTIFY_CARD
    assert StockAction.unpack(notify.callback_data).sku == "1"


# --- what picking one does --------------------------------------------------

def test_picking_a_product_sends_a_card_with_a_cart_link(db):
    """The message is sent by the customer — that is how inline mode works — so
    it says what was picked and carries the one button worth having."""
    _registered_customer(_order("1"), offers={"1": _offer("1", variant=99)})
    row = _ask(_Query()).results[0]
    assert "Product 1" in row.input_message_content.message_text
    button = row.reply_markup.inline_keyboard[0][0]
    assert urlparse(button.url).path == "/cart/99:1"


def test_a_tap_from_the_panel_is_counted_apart_from_the_screen(db):
    """Telegram reports nothing when a url button is tapped. The utm campaign
    is the only place the two entry points can be told apart."""
    _registered_customer(_order("1"), offers={"1": _offer("1")})
    row = _ask(_Query()).results[0]
    campaign = parse_qs(urlparse(row.reply_markup.inline_keyboard[0][0].url).query)
    assert campaign["utm_campaign"] == ["favourites_inline"]


def test_a_sold_out_product_offers_the_two_things_left_to_do(db):
    """Be told when it is back, and see what the shop says about it. A cart
    link would end at a checkout that refuses the order."""
    _registered_customer(_order("1"),
                         offers={"1": _offer("1", available=False, handle="serum")})
    row = _ask(_Query()).results[0]
    notify, site, _discount = (r[0] for r in row.reply_markup.inline_keyboard)
    assert notify.text == Texts("uk").BTN_NOTIFY_CARD
    assert StockAction.unpack(notify.callback_data) == StockAction(action="sub", sku="1")
    assert urlparse(site.url).path == "/products/serum"
    assert row.description.startswith("680 ₴ · зараз немає")


def test_a_product_already_waited_for_offers_to_stop_waiting(db):
    """The button must not offer again what the customer already asked for."""
    _registered_customer(_order("1"), offers={"1": _offer("1", available=False)})
    asyncio.run(add_stock_subscription(CHAT, "1", "Product 1"))
    row = _ask(_Query()).results[0]
    notify = row.reply_markup.inline_keyboard[0][0]
    assert notify.text == Texts("uk").BTN_WAITING_CARD
    assert StockAction.unpack(notify.callback_data).action == "unsub"


def test_the_card_escapes_what_it_interpolates(db):
    """Product names carry '&' by the thousand, and the card is sent as HTML."""
    order = _order("1")
    order["products_json"] = json.dumps([{"name": "Skin&Lab крем", "qty": 1, "sku": "1"}])
    _registered_customer(order, offers={"1": _offer("1")})
    row = _ask(_Query()).results[0]
    assert "Skin&amp;Lab" in row.input_message_content.message_text


# --- typing -----------------------------------------------------------------

def test_typing_filters_the_list(db):
    _registered_customer(_order("1", "2"),
                         offers={"1": _offer("1"), "2": _offer("2")})
    assert [row.id for row in _ask(_Query("product 2")).results] == ["2"]


def test_a_query_matching_nothing_says_so(db):
    _registered_customer(_order("1"), offers={"1": _offer("1")})
    query = _ask(_Query("тонер"))
    assert query.results == []
    assert query.kwargs["button"].text == Texts("uk").MSG_INLINE_NOTHING_FOUND


def test_opening_the_panel_is_counted_once_and_keystrokes_are_not(db, no_tracking):
    """Every character typed arrives as its own query. Counting each one would
    say more about typing speed than about whether anyone uses this."""
    _registered_customer(_order("1"), offers={"1": _offer("1")})
    _ask(_Query())
    _ask(_Query("p"))
    _ask(_Query("pr"))
    assert [event for event, _meta in no_tracking] == ["favourites_inline_opened"]


# --- the way back to the screen ---------------------------------------------

def test_the_list_carries_a_way_to_the_screen_it_replaced(db):
    """Both menus open this list from «⭐ Улюблені», so nothing else points at
    the favourites screen any more — and the discount request and the
    back-in-stock subscription live there. Neither can be a button in the list:
    a message sent through inline mode has no callback message to redraw."""
    _registered_customer(_order("1"), offers={"1": _offer("1")})
    query = _ask(_Query())
    assert query.results
    assert query.kwargs["button"].start_parameter == FAVOURITES_DEEP_LINK


def test_the_deep_link_opens_the_favourites_screen(db):
    """What the button above the list does: /start with a payload, straight to
    the screen, no menu in the way."""
    _registered_customer(_order("1"), offers={"1": _offer("1", variant=99)})

    sent = []

    async def answer(text, reply_markup=None, **kwargs):
        sent.append((text, reply_markup))

    async def nothing(*args, **kwargs):
        return None

    message = SimpleNamespace(
        chat=SimpleNamespace(id=CHAT),
        from_user=SimpleNamespace(id=CHAT, language_code="uk"),
        bot=SimpleNamespace(set_chat_menu_button=nothing),
        answer=answer,
    )
    asyncio.run(cmd_start(
        message,
        SimpleNamespace(args=FAVOURITES_DEEP_LINK),
        _config(),
        SimpleNamespace(clear=nothing),
        None,
        Texts("uk"),
        "uk",
        "uk",
    ))

    assert len(sent) == 1, "the screen and nothing else"
    _text, markup = sent[0]
    buttons = [b for row in markup.inline_keyboard for b in row]
    assert any(b.text.startswith("🛒 Product 1") for b in buttons)
    assert any(b.text == Texts("uk").BTN_WANT_DISCOUNT for b in buttons)


# --- asking for a discount from a card --------------------------------------

def _ask_for_discount(sku: str) -> dict:
    """Press 💰 on a card and report what the manager was sent."""
    told = {}

    async def send_message(chat_id, text, **kwargs):
        told["chat_id"], told["text"] = chat_id, text
        return SimpleNamespace(message_id=1)

    async def answer(text=None, **kwargs):
        told.setdefault("popup", text)

    callback = SimpleNamespace(
        from_user=SimpleNamespace(id=CHAT, first_name="Оксана", last_name="",
                                  username="oksana"),
        answer=answer,
        bot=SimpleNamespace(send_message=send_message),
    )
    asyncio.run(request_discount(
        callback, DiscountAction(action="ask", sku=sku), _config(), Texts("uk")
    ))
    return told


def test_the_manager_can_see_whose_request_it_is(db):
    """It used to say "chat_id: 1032317787" and nothing else, which is an
    identifier and not an answer — the manager had to go and look the number
    up before they could reply to a person."""
    _registered_customer(_order("1"), offers={"1": _offer("1")})
    told = _ask_for_discount("1")
    assert "@oksana" in told["text"]
    assert "+380670000000" in told["text"], "the number they are known by in the CRM"
    assert f'tg://user?id={CHAT}' in told["text"], "the name opens their profile"


def test_a_card_asks_for_a_discount_on_its_own_product(db):
    """The same button on the screen sits under the whole list and means all of
    it. On a card there is one product in front of the customer, and that is
    what the manager should be asked about."""
    _registered_customer(_order("1", "2"),
                         offers={"1": _offer("1"), "2": _offer("2")})
    told = _ask_for_discount("2")
    assert told["chat_id"] == SUPPORT
    assert "Product 2" in told["text"]
    assert "Product 1" not in told["text"]
    assert told["popup"] == Texts("uk").MSG_DISCOUNT_SENT


def test_a_discount_can_be_asked_for_beyond_the_top_five(db):
    """A card can come from the fortieth row. Asking the screen's question of
    the top five would answer "you have not bought that"."""
    skus = [str(n) for n in range(1, 9)]
    _registered_customer(_order(*skus), offers={s: _offer(s) for s in skus})
    assert "Product 8" in _ask_for_discount("8")["text"]


def test_a_sku_this_customer_never_bought_asks_nothing(db):
    """The products are read from their own history, which is what makes a
    forged sku a request for nothing rather than for somebody else's product."""
    _registered_customer(_order("1"), offers={"1": _offer("1")})
    assert "text" not in _ask_for_discount("999")
