"""The orders screen as blocks, and the plain screen it falls back to.

The shape being replaced was not a design: it was two limits. 4096 characters
made one order a card and the rest one line each; buttons living in a slab
under the whole message made `shown_id` necessary, so that the screen could
know which single order the "unfold" button meant. Blocks remove both, and what
these pin is that the removal is real — every order is its own foldable
section, and its buttons belong to it.
"""
from __future__ import annotations

import asyncio
import json

import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InputRichMessage

from bot import rich
from bot.handlers.orders import rich_orders_blocks
from core.i18n import customer_texts

T = customer_texts("uk")


def _order(idx: int = 1, **kw) -> dict:
    row = {
        "id": idx,
        "order_name": f"#2001{idx}",
        "source": "keycrm",
        "status_name": "Прибув у відділення",
        "status_group_id": 4,
        "grand_total": 2800,
        "currency": "грн",
        "ordered_at": f"2026-08-{10 + idx:02d}T10:00:00",
        "tracking_code": "20451496877635",
        "delivery_city": "Запоріжжя",
        "receive_point": "Відділення №20",
        "products_json": json.dumps(
            [{"name": "LALARECIPE Hempseed Serum", "qty": 1}], ensure_ascii=False),
    }
    row.update(kw)
    return row


def _types(blocks):
    return [b.model_dump(exclude_none=True, mode="json")["type"] for b in blocks]


def _sections(blocks):
    return [b for b in blocks if getattr(b, "type", None) == "details"]


# --- the shape ---------------------------------------------------------------

def test_every_order_is_its_own_foldable_section():
    """Not one card and a digest: the budget that forced that is gone."""
    blocks = rich_orders_blocks([_order(1), _order(2), _order(3)], T)
    assert len(_sections(blocks)) == 3


def test_the_newest_is_the_one_that_starts_open():
    blocks = rich_orders_blocks([_order(2), _order(1)], T)
    sections = _sections(blocks)
    assert sections[0].is_open is True
    assert sections[1].is_open is False


def test_the_screen_opens_with_a_real_heading():
    blocks = rich_orders_blocks([_order()], T)
    assert _types(blocks)[0] == "heading"
    assert blocks[0].text == T.MSG_ORDERS_TITLE
    assert "<b>" not in blocks[0].text, "a heading is a heading, not markup"


def test_no_orders_says_so_and_nothing_else():
    blocks = rich_orders_blocks([], T)
    assert len(blocks) == 1
    assert blocks[0].text == T.MSG_NO_ORDERS


# --- buttons belong to their order ------------------------------------------

def test_the_parcel_button_lives_inside_the_order_it_asks_about():
    """The point of the rewrite: before, this button sat under the whole
    message and had to carry an order id so the screen could tell what it
    meant."""
    blocks = rich_orders_blocks([_order(7)], T)
    inner = _sections(blocks)[0].blocks
    buttons = [b for b in inner if getattr(b, "type", None) == "buttons"]
    assert len(buttons) == 1
    button = buttons[0].buttons[0]
    assert button.text == T.BTN_WHERE_PARCEL
    assert button.callback_data.startswith("ord:track:7:")


def test_an_order_with_no_parcel_offers_no_parcel_button():
    blocks = rich_orders_blocks([_order(tracking_code="")], T)
    inner = _sections(blocks)[0].blocks
    assert not [b for b in inner if getattr(b, "type", None) == "buttons"]


def test_a_parcel_already_answered_replaces_the_button_with_the_answer():
    blocks = rich_orders_blocks([_order(1)], T, parcels={1: ["Статус: Отримано"]})
    inner = _sections(blocks)[0].blocks
    assert not [b for b in inner if getattr(b, "type", None) == "buttons"]
    assert any(getattr(b, "text", None) == "Статус: Отримано" for b in inner)


# --- content -----------------------------------------------------------------

def test_the_full_product_name_survives():
    """The 40-character cut exists because a plain line competes with five
    other labelled lines inside 4096 characters. A list item does not."""
    long = ("NEOGEN DERMALOGY High R Exosome Cream Відновлювальний крем "
            "з екзосомами Cica, 40 мл")
    blocks = rich_orders_blocks([_order(products_json=json.dumps(
        [{"name": long, "qty": 1}], ensure_ascii=False))], T)
    listed = _sections(blocks)[0].blocks[1].items[0].blocks[0].text
    assert listed.startswith(long), "the name was cut where nothing was pressing"


def test_the_tracking_number_is_a_link_not_a_string():
    blocks = rich_orders_blocks([_order()], T)
    paragraph = next(b for b in _sections(blocks)[0].blocks
                     if getattr(b, "type", None) == "paragraph"
                     and isinstance(b.text, list)
                     and any(getattr(part, "url", None) for part in b.text
                             if not isinstance(part, str)))
    link = next(p for p in paragraph.text if not isinstance(p, str))
    assert link.text == "20451496877635"
    assert "novaposhta.ua" in link.url


def test_cancelled_orders_sit_below_a_divider_not_among_the_rest():
    blocks = rich_orders_blocks(
        [_order(1), _order(2, status_group_id=6, status_name="canceled")], T)
    assert len(_sections(blocks)) == 1, "a cancelled order is not a section"
    assert "divider" in _types(blocks)


# --- limits ------------------------------------------------------------------

def test_a_long_history_is_capped_and_says_so():
    """Silence would read as "that is all of them"."""
    blocks = rich_orders_blocks([_order(i) for i in range(1, 400)], T)
    assert rich.weigh(blocks) <= rich.TEXT_BUDGET
    assert len(_sections(blocks)) < 399
    tail = blocks[-1]
    assert getattr(tail, "type", None) == "paragraph"
    assert "з 399" in tail.text


def test_the_whole_screen_is_a_valid_rich_message():
    """The reason for building blocks instead of HTML: this fails here, in our
    process, rather than as a 400 that costs the customer the screen."""
    blocks = rich_orders_blocks([_order(1), _order(2)], T)
    payload = InputRichMessage(blocks=blocks).model_dump(
        exclude_none=True, mode="json")
    assert payload["blocks"][0]["type"] == "heading"
    assert payload["blocks"][1]["type"] == "details"


# --- the plain screen is half the feature ------------------------------------

class _Bot:
    def __init__(self, rich_fails: Exception | None = None) -> None:
        self.rich: list = []
        self.plain: list = []
        self._fail = rich_fails

    async def send_rich_message(self, chat_id, rich_message):
        if self._fail:
            raise self._fail
        self.rich.append((chat_id, rich_message))
        return "rich"

    async def send_message(self, chat_id, text, reply_markup=None):
        self.plain.append((chat_id, text, reply_markup))
        return "plain"


def test_the_rich_screen_goes_when_telegram_takes_it():
    bot = _Bot()
    result = asyncio.run(rich.send(bot, 5, [rich.para("hi")], plain="hi"))
    assert result == "rich"
    assert bot.plain == []


def test_a_refused_rich_message_becomes_the_plain_screen():
    """A client too old to draw blocks is a real reader of this bot, and so is
    the day Telegram changes what it accepts."""
    bot = _Bot(rich_fails=TelegramBadRequest(method=None, message="bad block"))
    result = asyncio.run(rich.send(bot, 5, [rich.para("hi")],
                                   plain="📦 Твої замовлення"))
    assert result == "plain"
    assert bot.plain[0][1] == "📦 Твої замовлення"


def test_an_aiogram_without_rich_messages_still_delivers():
    class _Old:
        def __init__(self):
            self.plain = []

        async def send_message(self, chat_id, text, reply_markup=None):
            self.plain.append(text)
            return "plain"

    bot = _Old()
    assert asyncio.run(rich.send(bot, 5, [rich.para("hi")], plain="fallback")) == "plain"
    assert bot.plain == ["fallback"]
