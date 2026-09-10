"""The digest: one order in full, the rest a line each.

The screen this replaced printed every order as six labelled lines — five
orders came to 42 lines and 901 characters, twenty of which were the words
"Статус:", "Товари:", "Сума:" and "Дата:" — and the verdict on it was that you
get lost in it. So most of these tests are about size and about scanning: what
a line says, what the card says, and what is folded away.
"""
from __future__ import annotations

import asyncio
import json
import re

import pytest

from core.repos import base as repos_base
from core.repos.orders import upsert_orders
from core.repos.schema import init_db
from core.repos.users import save_user

from bot import rich
from bot.handlers import orders
from bot.handlers.orders import _format_orders_from_cache, _orders_kb
from core.i18n import Texts

T = Texts("uk")


@pytest.fixture()
def db_with_orders(tmp_path, monkeypatch):
    """The background refresh re-reads the cache, so it needs one."""
    monkeypatch.setattr(repos_base, "DB_PATH", str(tmp_path / "bot_data.db"))
    asyncio.run(init_db())
    asyncio.run(save_user(1, "+380670000000"))
    asyncio.run(upsert_orders(1, [
        {**row, "chat_id": 1, "source": "keycrm",
         "source_order_id": str(row["id"]), "external_id": str(row["id"]),
         "buyer_name": "", "payment_status": "", "recipient_name": ""}
        for row in HISTORY
    ]))


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


# --- the parcel status that arrives by itself -------------------------------

class _Sent:
    """As much of a Message as the background refresh touches."""

    def __init__(self):
        self.edits: list[dict] = []

    async def edit_text(self, text, reply_markup=None, **kw):
        self.edits.append({"text": text, "markup": reply_markup})


class _NovaPoshta:
    """The carrier, answering from a dict."""

    def __init__(self, answers=None, ttn_seen=None):
        self.answers = answers or {}
        self.asked: list[list[str]] = []

    async def track_many(self, ttns, phone=""):
        self.asked.append(list(ttns))
        return {ttn: self.answers[ttn] for ttn in ttns if ttn in self.answers}


def _status(**kw):
    from types import SimpleNamespace
    base = {"status": "in_transit", "warehouse_recipient": "Відділення №12",
            "actual_delivery": "", "recipient_date": "",
            "scheduled_delivery": "29-08-2026 12:00:00"}
    return SimpleNamespace(**{**base, **kw})


def _run_refresh(db_orders, novaposhta, **state):
    """Run the background half directly — the spawn is the handler's business."""
    import asyncio

    from bot.handlers import orders as module

    sent = _Sent()
    asyncio.run(module._fill_in_parcel(
        sent, 1, T, novaposhta,
        **{"shown_id": 0, "page": 0, "cancelled": False, "expanded": False, **state}))
    return sent


def test_the_parcel_status_arrives_without_being_asked_for(db_with_orders):
    """The screen opens from the cache instantly, and the carrier's answer —
    which costs a second or three — edits itself in a moment later."""
    np = _NovaPoshta({"59000123456": _status()})
    sent = _run_refresh(HISTORY, np)
    assert np.asked == [["59000123456"]]
    body = _plain(sent.edits[0]["text"])
    assert "Відділення №12" in body
    assert "29.08.2026" in body


def test_the_answer_takes_the_button_with_it(db_with_orders):
    np = _NovaPoshta({"59000123456": _status()})
    sent = _run_refresh(HISTORY, np)
    assert T.BTN_WHERE_PARCEL not in _labels(sent.edits[0]["markup"])


def test_a_parcel_the_carrier_cannot_place_leaves_the_screen_alone(db_with_orders):
    """Nova Poshta not answering is ordinary. The screen stays as it was, with
    the button still offering to ask."""
    sent = _run_refresh(HISTORY, _NovaPoshta({}))
    assert sent.edits == []


def test_a_newer_lookup_cancels_the_one_still_in_flight(db_with_orders):
    """Open the screen, tap another order a second later: without this the
    first answer lands afterwards and puts the old card back."""
    from bot.handlers import orders as module

    class _Slow:
        async def track_many(self, ttns, phone=""):
            await asyncio.sleep(5)
            return {}

    async def run():
        sent = _Sent()
        module.follow_up_parcel(sent, 1, T, _Slow())
        first = module._parcel_tasks[1]
        await asyncio.sleep(0.05)          # let it get as far as the lookup

        module.follow_up_parcel(sent, 1, T, _Slow(), shown_id=4)
        second = module._parcel_tasks[1]
        await asyncio.sleep(0)
        assert first.cancelled(), "the stale lookup is dropped"
        second.cancel()

    asyncio.run(run())


# --- a history that is actually long ----------------------------------------

def _long_history(count: int = 50, cancelled_every: int = 8) -> list[dict]:
    history = [_order(1, at="2026-08-20T10:00:00", total=3962, items=9,
                      status="in_transit", ttn="59000123456")]
    for n in range(2, count + 1):
        history.append(_order(
            n, at=f"2026-{12 - (n % 12):02d}-{(n % 27) + 1:02d}T10:00:00",
            total=500 + n * 37, items=(n % 6) + 1,
            status="canceled" if n % cancelled_every == 0 else "completed",
            group=6 if n % cancelled_every == 0 else 1))
    return history


def test_fifty_orders_still_fit_on_a_phone():
    """The question this screen exists to answer. Text well inside Telegram's
    4096, and — the part that bites first — a keyboard that is not half a
    screen of buttons under the message."""
    history = _long_history()
    text = _format_orders_from_cache(history, T)
    rows = _orders_kb(history, T).inline_keyboard

    assert len(text) < 1500
    assert len(_plain(text).splitlines()) <= 25
    assert len(rows) <= 7, "seven rows, of which three are the dates"
    assert sum(len(row) for row in rows) <= 20


def test_the_two_buttons_about_the_card_share_a_row():
    history = _long_history()
    rows = _orders_kb(history, T).inline_keyboard
    assert [b.text for b in rows[1]] == [T.BTN_SHOW_ITEMS.format(count=9),
                                         T.BTN_WHERE_PARCEL]


def test_show_more_of_the_same_shares_a_row_too():
    history = _long_history()
    labels = [[b.text for b in row] for row in _orders_kb(history, T).inline_keyboard]
    assert [T.BTN_CANCELLED_SHOW.format(count=6), T.BTN_ORDERS_OLDER] in labels


class _Keycrm:
    """A CRM with nothing in it. Reached only when the cache is cold, which is
    exactly the empty-history case below."""

    async def get_orders_by_phone(self, *a, **kw):
        return []

    async def get_buyer_by_phone(self, *a, **kw):
        return None


def _anchor():
    """orders_screen touches the anchor only to show "typing…"."""
    from types import SimpleNamespace

    async def _noop(*a, **kw):
        return None

    return SimpleNamespace(chat=SimpleNamespace(id=1),
                           bot=SimpleNamespace(send_chat_action=_noop))


# --- both shapes come out of one call ----------------------------------------
#
# The screen is sent rich where the client can draw it and plain where it
# cannot, and there is no way to tell the two apart in advance — Telegram
# answers 200 either way and the degrading happens on the device. So both
# forms are built every time, and neither may be an afterthought.


def _admin_config(*ids):
    from types import SimpleNamespace
    return SimpleNamespace(env=SimpleNamespace(admin_ids=list(ids)),
                           first_order_discount=None)


def test_the_screen_carries_both_shapes(db_with_orders):
    screen = asyncio.run(orders.orders_screen(1, T, _Keycrm(), _anchor(),
                                              _admin_config(1)))
    assert screen.text, "the plain screen is a real screen, not a placeholder"
    assert screen.blocks, "and the rich one is built in the same call"
    assert screen.markup is not None


def test_every_customer_gets_the_rich_screen_now(db_with_orders):
    """The admin gate is gone. It stood while the screen was raw and while
    nobody had looked at an older client; the owner settled the second on
    2026-09-10 by deciding every customer is on a current Telegram
    (docs/rich-messages.md), and the first was fixed before it was lifted.

    What it bought while it stood: twenty-six review findings, a hole in the
    gate itself, and three counters that lied — all met by an admin."""
    screen = asyncio.run(orders.orders_screen(1, T, _Keycrm(), _anchor(),
                                              _admin_config(999)))
    assert screen.blocks, "no longer only for admins"
    assert screen.text, "and the plain form is still built beside it"


def test_blocks_do_not_need_a_config(db_with_orders):
    """The internal redraws pass none, and they must draw the same screen."""
    screen = asyncio.run(orders.orders_screen(1, T, _Keycrm(), _anchor()))
    assert screen.blocks


def test_the_rich_shape_is_a_valid_rich_message(db_with_orders):
    """It fails here, in our process, rather than as a 400 that costs the
    customer the whole screen."""
    from aiogram.types import InputRichMessage
    screen = asyncio.run(orders.orders_screen(1, T, _Keycrm(), _anchor(),
                                              _admin_config(1)))
    payload = InputRichMessage(blocks=screen.blocks)
    assert payload.model_dump(exclude_none=True, mode="json")["blocks"]
    assert rich.fits(screen.blocks)


def test_an_empty_history_has_no_rich_shape(tmp_path, monkeypatch):
    """A paragraph and a button is a paragraph and a button in either shape,
    and the plain one already carries the support button that matters."""
    monkeypatch.setattr(repos_base, "DB_PATH", str(tmp_path / "empty.db"))
    asyncio.run(init_db())
    asyncio.run(save_user(2, "+380670000001"))
    screen = asyncio.run(orders.orders_screen(2, T, _Keycrm(), _anchor()))
    assert screen.blocks is None
    assert screen.text


def test_a_customer_with_no_number_gets_no_blocks_either(tmp_path, monkeypatch):
    monkeypatch.setattr(repos_base, "DB_PATH", str(tmp_path / "nonum.db"))
    asyncio.run(init_db())
    screen = asyncio.run(orders.orders_screen(3, T, _Keycrm(), _anchor()))
    assert screen.blocks is None
    # It used to assert `markup is None` as well, which pinned the dead end
    # rather than the behaviour: a screen with nothing to tap was the thing
    # worth fixing, not a property worth keeping.
    assert screen.markup is not None


# --- the last dead end -------------------------------------------------------
#
# Both order screens answered "no phone stored" with a bare string and no
# keyboard. Nothing to tap, and the reply keyboard underneath cannot help: each
# of its keys lands back on a screen that needs the number. The words were
# borrowed too — ERR_PHONE_NOT_FOUND says "we found no orders for this number",
# about a number we do not have.


def _no_phone_db(tmp_path, monkeypatch):
    monkeypatch.setattr(repos_base, "DB_PATH", str(tmp_path / "bot_data.db"))
    asyncio.run(init_db())


def test_a_screen_with_no_phone_has_a_way_out(tmp_path, monkeypatch):
    _no_phone_db(tmp_path, monkeypatch)
    screen = asyncio.run(orders.orders_screen(1, T, _Keycrm(), _anchor()))
    assert screen.markup is not None, "the one dead end left in the bot"
    labels = _labels(screen.markup)
    assert T.BTN_CHANGE_PHONE in labels, "the button that actually fixes it"
    assert T.BTN_SUPPORT in labels, "for an order under a different number"
    assert T.BTN_MENU in labels


def test_it_says_we_have_no_number_not_that_hers_found_nothing(tmp_path, monkeypatch):
    _no_phone_db(tmp_path, monkeypatch)
    screen = asyncio.run(orders.orders_screen(1, T, _Keycrm(), _anchor()))
    assert screen.text == T.MSG_NO_PHONE_YET
    assert screen.text != T.ERR_PHONE_NOT_FOUND


def test_favourites_has_the_same_way_out(tmp_path, monkeypatch):
    _no_phone_db(tmp_path, monkeypatch)
    text, markup = asyncio.run(
        orders.favourites_screen(1, T, _Keycrm(), _anchor(), ""))[:2]
    assert text == T.MSG_NO_PHONE_YET
    assert markup is not None
