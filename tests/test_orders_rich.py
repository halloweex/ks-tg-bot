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


def test_cancelled_orders_are_hidden_until_asked_for():
    """Same switch the plain screen has, and it has to mean the same thing:
    its button sits in the slab under the rich screen too, and a button that
    redraws without changing anything reads as broken — Telegram answers
    "message is not modified" and nothing moves."""
    rows = [_order(1), _order(2, status_group_id=6, status_name="canceled")]

    folded = rich_orders_blocks(rows, T)
    assert len(_sections(folded)) == 1, "a cancelled order is not a section"
    assert "divider" not in _types(folded), "nothing to divide while they are hidden"

    shown = rich_orders_blocks(rows, T, cancelled=True)
    assert "divider" in _types(shown)
    assert len(_sections(shown)) == 1, "still not a section — a line below the rule"
    assert len(shown) > len(folded)


# --- limits ------------------------------------------------------------------

def test_a_long_history_is_capped_and_says_so():
    """Silence would read as "that is all of them"."""
    blocks = rich_orders_blocks([_order(i) for i in range(1, 400)], T)
    assert rich.fits(blocks), "the cap must hold for the finished screen, tail included"
    assert len(_sections(blocks)) < 399
    said = [b for b in blocks if getattr(b, "type", None) == "paragraph"
            and "з 399" in str(getattr(b, "text", ""))]
    assert said, "it must say how many it left out"
    assert getattr(blocks[-1], "type", None) == "buttons", (
        "the way back goes last, after everything the screen says"
    )


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

    async def send_rich_message(self, chat_id, rich_message, reply_markup=None,
                                disable_notification=None, message_effect_id=None):
        if self._fail:
            raise self._fail
        self.rich.append((chat_id, rich_message, reply_markup,
                          disable_notification, message_effect_id))
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


# --- what phase 0 fixed ------------------------------------------------------
#
# Each of these pins a defect that was found by reading rather than by running:
# the rich path had never been exercised against a real Telegram, so nothing
# here failed until it was looked for.


def test_the_keyboard_rides_along_with_the_blocks():
    """The screen was a dead end: send_rich_message takes a reply_markup and
    the first version dropped it, which looked like Telegram not supporting
    one. Without this the customer has no way back at all."""
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="📋 Меню", callback_data="menu")]])
    bot = _Bot()
    asyncio.run(rich.send(bot, 1, [rich.para("x")], plain="x", reply_markup=kb))
    _chat, _msg, markup, _silent, _effect = bot.rich[0]
    assert markup is kb


def test_quiet_hours_and_the_effect_survive_the_rich_path():
    """Both are properties of the message, not of its shape. A screen that
    loses them on the way to rich is a regression nobody asked for."""
    bot = _Bot()
    asyncio.run(rich.send(bot, 1, [rich.para("x")], plain="x",
                          disable_notification=True, message_effect_id="5104"))
    _chat, _msg, _markup, silent, effect = bot.rich[0]
    assert silent is True
    assert effect == "5104"


def test_a_block_handed_to_bullets_is_refused_here():
    """The dangerous case, because it is not an error anywhere else: a block in
    a rich-text position serialises to debris instead of being rejected, and
    the customer sees the wreckage. bullets() is the obvious way in."""
    with pytest.raises(TypeError, match="not blocks"):
        rich.bullets([rich.para("a block, not rich text")])


def test_inline_markup_is_not_counted_as_blocks():
    """count_blocks reads `type` against the enum. Counting every mapping calls
    bold runs and links blocks and overstates a screen by about a third — which
    is how the old budget managed to pass 530 real blocks."""
    from aiogram.types import RichTextBold
    plain = [rich.para("just text")]
    marked = [rich.para(["before ", RichTextBold(text="bold"), " after"])]
    assert rich.count_blocks(plain) == rich.count_blocks(marked) == 1


def test_the_budget_counts_bytes_not_characters():
    """Ukrainian is two bytes a letter, so counting characters would let a
    screen through at twice the size Telegram actually measures."""
    assert rich.text_bytes([rich.para("абв")]) > 3


# --- no entrance may write plain over a rich screen --------------------------
#
# The first attempt at the migration claimed six entrances and delivered five.
# show_order was missed, and every button in the slab under a rich screen is
# one of its callbacks — so the first tap on a neighbouring order destroyed the
# blocks. Nothing failed: the suite was green and the defect shipped as far as
# a local commit. This is the test that would have caught it.


def _order_action_buttons(markup) -> list[str]:
    return [b.callback_data for row in markup.inline_keyboard for b in row
            if (b.callback_data or "").startswith("ord:")]


def test_every_order_button_in_the_slab_has_a_rich_aware_handler():
    """A structural check rather than a behavioural one, because the failure is
    structural: a handler that forgets `blocks=` cannot be seen from outside."""
    import inspect

    from bot.handlers import orders as mod

    rows = [_order(1), _order(2), _order(3)]
    assert _order_action_buttons(mod._orders_kb(rows, T)), "the slab has ord: buttons"

    # Every handler registered for OrderAction must pass blocks to render().
    for name in ("show_order", "track_parcel"):
        src = inspect.getsource(getattr(mod, name))
        assert "blocks=" in src, (
            f"{name} draws the orders screen without blocks — a tap through it "
            f"would write plain text over a rich screen and destroy it"
        )


def test_the_background_parcel_fill_in_keeps_the_keyboard():
    """editMessageText with no reply_markup takes the keyboard away, and the
    rich branch had none: the slab vanished a second after the screen opened."""
    import inspect

    from bot.handlers import orders as mod

    src = inspect.getsource(mod._fill_in_parcel)
    rich_branch = src.split("rich.edit(")[1].split("else:")[0]
    assert "reply_markup" in rich_branch
