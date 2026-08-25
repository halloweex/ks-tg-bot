"""The digest: one order in full, the rest a line each.

The screen this replaced printed every order as six labelled lines — five
orders came to 42 lines and 901 characters, twenty of which were the words
"Статус:", "Товари:", "Сума:" and "Дата:" — and the verdict on it was that you
get lost in it. So most of these tests are about size and about scanning: what
a line says, what the card says, and what is folded away.
"""
from __future__ import annotations

import json
import re

from bot.handlers.orders import _format_orders_from_cache, _orders_kb
from core.i18n import Texts

T = Texts("uk")


def _order(order_id: int, *, at: str, total: float = 1465, items: int = 3,
           status: str = "completed", group: int = 1, ttn: str = "",
           name: str = "") -> dict:
    return {
        "id": order_id, "order_name": name, "status_name": status,
        "status_group_id": group, "grand_total": total, "currency": "грн",
        "ordered_at": at, "tracking_code": ttn, "shipping_status": "",
        "delivery_city": "", "receive_point": "",
        "products_json": json.dumps([{"name": f"Засіб {n}", "qty": 1, "sku": str(n)}
                                     for n in range(items)]),
    }


def _plain(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text)


def _digest_lines(body: str) -> list[str]:
    """The one-line orders: a glyph, a date, and two dots between three facts."""
    return [line for line in body.splitlines()
            if re.match(r"^[✅🚚🕐❌] \d\d\.\d\d\.\d{4} · ", line)]


def _labels(markup) -> list[str]:
    return [b.text for row in markup.inline_keyboard for b in row]


HISTORY = [
    _order(1, at="2026-08-20T10:00:00", total=3962, items=9,
           status="in_transit", ttn="59000123456"),
    _order(2, at="2026-06-15T10:00:00", total=1465, items=3),
    _order(3, at="2026-03-02T10:00:00", total=890, items=1),
    _order(4, at="2026-01-28T10:00:00", total=2450, items=5, name="#19966"),
    _order(5, at="2025-12-01T10:00:00", total=700, items=2,
           status="canceled", group=6),
]


# --- what the screen is -----------------------------------------------------

def test_five_orders_are_a_card_and_three_lines():
    """The measurement the redesign came from: this used to be 42 lines."""
    body = _plain(_format_orders_from_cache(HISTORY, T))
    assert len(body.splitlines()) < 20
    # The three active orders that are not the card, one line each.
    assert len(_digest_lines(body)) == 3


def test_a_line_says_when_how_much_and_how_many():
    body = _plain(_format_orders_from_cache(HISTORY, T))
    assert "✅ 15.06.2026 · 1 465 грн · 3 товари" in body
    assert "✅ 02.03.2026 · 890 грн · 1 товар" in body


def test_the_newest_order_is_the_card():
    body = _plain(_format_orders_from_cache(HISTORY, T))
    assert "Статус:" in body, "the card is the only block with labels"
    assert body.count("Статус:") == 1
    assert "20.08.2026" in body.split("✅ 15.06")[0]


def test_the_card_does_not_open_on_twenty_product_names():
    """A card is the detail view, not a reason for the screen to start with a
    wall. Above four items it is folded, and the keyboard offers to unfold."""
    body = _plain(_format_orders_from_cache(HISTORY, T))
    assert "…та інші (7)" in body
    assert T.BTN_SHOW_ITEMS.format(count=9) in _labels(_orders_kb(HISTORY, T))


def test_unfolding_the_card_shows_every_item():
    body = _plain(_format_orders_from_cache(HISTORY, T, expanded=True))
    assert "Засіб 8 ×1" in body
    assert T.BTN_HIDE_ITEMS in _labels(_orders_kb(HISTORY, T, expanded=True))


# --- moving between orders --------------------------------------------------

def test_every_line_has_a_button_that_makes_it_the_card():
    """Labelled with the same glyph and date the line carries, so nothing on
    the screen has to be matched to a number — which is what the old "🔎 3"
    buttons demanded, plus a legend to explain them."""
    labels = _labels(_orders_kb(HISTORY, T))
    assert "✅ 15.06.2026" in labels and "✅ 02.03.2026" in labels


def test_asking_for_another_order_draws_that_one_in_full():
    body = _plain(_format_orders_from_cache(HISTORY, T, shown_id=4))
    assert "#19966" in body.split("\n\n")[1], "the card is the one asked for"
    assert "🚚 20.08.2026" in body, "and the newest becomes a line"


def test_an_id_that_is_not_theirs_falls_back_to_the_newest():
    """The list is re-read for the caller's own chat, so a forged id names no
    order of theirs — and the screen still has to draw something."""
    body = _plain(_format_orders_from_cache(HISTORY, T, shown_id=999))
    assert "20.08.2026" in body.split("✅ 15.06")[0]


# --- the ones that never happened -------------------------------------------

def test_cancelled_orders_are_folded_away_and_counted():
    body = _plain(_format_orders_from_cache(HISTORY, T))
    assert "01.12.2025" not in body
    assert T.BTN_CANCELLED_SHOW.format(count=1) in _labels(_orders_kb(HISTORY, T))


def test_cancelled_orders_can_be_looked_at():
    body = _plain(_format_orders_from_cache(HISTORY, T, cancelled=True))
    assert "❌ 01.12.2025 · 700 грн · 2 товари" in body
    assert T.BTN_CANCELLED_HIDE in _labels(_orders_kb(HISTORY, T, cancelled=True))


def test_a_cancelled_order_is_never_the_card():
    """It is not history anybody is looking for, and it is not the newest thing
    that happened — it is the newest thing that did not."""
    body = _plain(_format_orders_from_cache(HISTORY, T, shown_id=5))
    assert body.count("Статус:") == 1
    assert "20.08.2026" in body.split("✅ 15.06")[0]


# --- the parcel -------------------------------------------------------------

def test_the_card_offers_to_ask_where_the_parcel_is():
    """This is the screen that used to be a second entry in the menu. One
    lookup, for one parcel, when asked for."""
    assert T.BTN_WHERE_PARCEL in _labels(_orders_kb(HISTORY, T))


def test_an_order_with_no_number_has_nothing_to_ask_about():
    assert T.BTN_WHERE_PARCEL not in _labels(_orders_kb(HISTORY, T, shown_id=2))


def test_the_answer_lands_on_the_card_and_replaces_the_button():
    lines = ["Статус: В дорозі", "🏤 Відділення №12"]
    body = _plain(_format_orders_from_cache(HISTORY, T, parcel=lines))
    assert "🏤 Відділення №12" in body
    assert T.BTN_WHERE_PARCEL not in _labels(_orders_kb(HISTORY, T, parcel=True))


# --- paging -----------------------------------------------------------------

def test_a_long_history_pages_by_ten():
    history = [_order(n, at=f"2026-01-{n:02d}T10:00:00") for n in range(1, 26)]
    body = _plain(_format_orders_from_cache(history, T))
    assert "Показано 1–10 з 25" in body
    assert T.BTN_ORDERS_OLDER in _labels(_orders_kb(history, T))


def test_an_empty_history_says_so():
    assert _format_orders_from_cache([], T) == T.MSG_NO_ORDERS
