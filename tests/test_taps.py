"""The tap journal: which press was recorded, and what was never written down."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

import bot.taps as taps
from bot.taps import TAP, TapJournal, what_was_pressed
from core import texts
from core.i18n import Texts

CHAT = 501


def _message(text=None, *, chat_type="private", content_type="text"):
    return SimpleNamespace(chat=SimpleNamespace(id=CHAT, type=chat_type), text=text,
                           content_type=content_type)


def _press(data, *, buttons=(), chat_type="private", inline=False):
    markup = SimpleNamespace(inline_keyboard=[[SimpleNamespace(text=t, callback_data=d)
                                               for t, d in buttons]])
    message = None if inline else SimpleNamespace(
        chat=SimpleNamespace(id=CHAT, type=chat_type), reply_markup=markup)
    return SimpleNamespace(data=data, from_user=SimpleNamespace(id=CHAT), message=message)


# --- buttons -------------------------------------------------------------------

def test_an_inline_button_is_its_prefix_and_action_and_what_it_said():
    pressed = what_was_pressed(_press(
        "menu:open_orders", buttons=[("📦 Замовлення", "menu:open_orders")]))
    assert pressed == (CHAT, "menu:open_orders", "📦 Замовлення")


def test_an_order_id_and_a_page_are_not_part_of_which_button():
    """`ord:<action>:<order_id>:<page>:<state>` — the id is an order, the page
    is where; neither is *which button*, and both would split one row in two."""
    pressed = what_was_pressed(_press(
        "ord:open:4711:2:c", buttons=[("Замовлення 18.09", "ord:open:4711:2:c")]))
    assert pressed[1] == "ord:open"
    assert pressed[2] == "Замовлення ##.##", "digits blanked, one row per button"


def test_a_press_on_an_inline_mode_message_is_still_recorded():
    assert what_was_pressed(_press("stk:sub:SKU-1", inline=True))[1] == "stk:sub"


def test_a_press_in_the_support_group_is_a_manager_not_a_customer():
    assert what_was_pressed(_press("menu:support", chat_type="supergroup")) is None


# --- the keyboard below the input --------------------------------------------------

@pytest.mark.parametrize("lang", ["uk", "en"])
def test_a_reply_key_is_one_row_whatever_the_language(lang):
    label = Texts(lang).BTN_ORDERS
    assert what_was_pressed(_message(label)) == (CHAT, "key:orders", texts.BTN_ORDERS)


def test_a_command_keeps_the_command_and_drops_its_payload():
    """`/start ref_abc` carries a referral code; the row is `/start`."""
    assert what_was_pressed(_message("/start ref_abc123"))[1:] == ("/start", "/start")
    assert what_was_pressed(_message("/stats@koreanstory_bot"))[1] == "/stats"


# --- what is never written ---------------------------------------------------------

def test_typed_text_is_recorded_as_text_and_never_as_its_content():
    """A phone number typed at the share step, a question for the manager —
    none of it belongs in an analytics table."""
    for typed in ("+380501234567", "Де моє замовлення? Мене звати Оля"):
        chat, what, label = what_was_pressed(_message(typed))
        assert what == "text"
        assert typed not in label and "380" not in label


def test_a_contact_or_a_photo_is_its_kind():
    assert what_was_pressed(_message(None, content_type="contact"))[1] == "contact"
    assert what_was_pressed(_message(None, content_type="photo"))[1] == "photo"


def test_a_group_message_is_not_recorded():
    assert what_was_pressed(_message("📦 Замовлення", chat_type="group")) is None


# --- the middleware ---------------------------------------------------------------

def test_the_middleware_records_and_always_runs_the_handler(monkeypatch):
    recorded = []
    monkeypatch.setattr(taps, "track", lambda chat, event, **meta: recorded.append((chat, event, meta)))

    async def handler(event, data):
        return "handled"

    result = asyncio.run(TapJournal()(handler, _message(Texts("uk").BTN_SUPPORT), {}))
    assert result == "handled"
    assert recorded == [(CHAT, TAP, {"what": "key:support", "label": texts.BTN_SUPPORT})]


def test_a_failing_journal_never_costs_the_update(monkeypatch):
    def boom(*_args, **_kwargs):
        raise RuntimeError("disk full")
    monkeypatch.setattr(taps, "track", boom)

    async def handler(event, data):
        return "handled"

    assert asyncio.run(TapJournal()(handler, _message("hello"), {})) == "handled"
