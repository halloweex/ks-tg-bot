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
CHAT = 4242


@pytest.fixture()
def db_with_orders(tmp_path, monkeypatch):
    """The handlers re-read the cache for the caller's own chat, so driving one
    needs a database with something in it."""
    from core.repos import base as repos_base
    from core.repos.orders import upsert_orders
    from core.repos.schema import init_db
    from core.repos.users import save_user

    monkeypatch.setattr(repos_base, "DB_PATH", str(tmp_path / "bot_data.db"))
    asyncio.run(init_db())
    asyncio.run(save_user(CHAT, "+380670000000"))
    asyncio.run(upsert_orders(CHAT, [
        {**_order(i), "chat_id": CHAT, "source_order_id": str(i),
         "external_id": str(i), "buyer_name": "", "payment_status": "",
         "recipient_name": "", "tracking_code": "59000123456" if i == 1 else ""}
        for i in (1, 2, 3)
    ]))


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

    async def send_message(self, chat_id, text, reply_markup=None, **kw):
        # **kw on purpose: a narrow fake is how the fallback quietly lost
        # disable_notification and message_effect_id for so long.
        self.plain.append((chat_id, text, reply_markup))
        self.plain_kw = kw
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

        async def send_message(self, chat_id, text, reply_markup=None, **kw):
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


def _rich_anchor(monkeypatch):
    """A real Message that is a rich screen, with its edit intercepted.

    The tests this replaced read the handlers' source for the substring
    `blocks=`. That catches a rename and nothing else: four separate mutations
    went through the suite untouched. What matters is not that the word appears
    but that a rich_message goes out — so this drives the handler and reads the
    request.
    """
    from aiogram.types import Message as _M

    sent: list[dict] = []
    msg = _M.model_validate({
        "message_id": 10, "date": 0, "chat": {"id": CHAT, "type": "private"},
        "text": "before",
        "rich_message": {"blocks": [{"type": "paragraph", "text": "x"}]},
    })

    async def fake_edit(self, text=None, reply_markup=None, **kw):
        sent.append({"text": text, "rich_message": kw.get("rich_message"),
                     "reply_markup": reply_markup})
        return self

    monkeypatch.setattr(_M, "edit_text", fake_edit)
    return msg, sent


def _callback(msg, data: str):
    from types import SimpleNamespace
    return SimpleNamespace(
        message=msg, data=data, bot=None,
        from_user=SimpleNamespace(id=CHAT, language_code="uk"),
        answer=_noop,
    )


async def _noop(*a, **kw):
    return None


def test_a_tap_on_a_neighbouring_order_keeps_the_screen_rich(monkeypatch, db_with_orders):
    """The defect that shipped as far as a local commit: show_order drew plain
    text, and every button in the slab under a rich screen is one of its
    callbacks, so the first tap destroyed the blocks."""
    from bot.callbacks import OrderAction
    from bot.handlers import orders as mod

    msg, sent = _rich_anchor(monkeypatch)
    asyncio.run(mod.show_order(
        _callback(msg, "ord:show:2:0:"),
        OrderAction(action="show", order_id=2, page=0, state=""),
        None, T))

    assert sent, "the screen was redrawn"
    assert sent[0]["rich_message"] is not None, (
        "plain text over a rich anchor destroys the blocks, silently")
    assert sent[0]["text"] is None


def test_asking_where_the_parcel_is_keeps_the_screen_rich(monkeypatch, db_with_orders):
    from bot.callbacks import OrderAction
    from bot.handlers import orders as mod

    msg, sent = _rich_anchor(monkeypatch)
    asyncio.run(mod.track_parcel(
        _callback(msg, "ord:track:1:0:"),
        OrderAction(action="track", order_id=1, page=0, state=""),
        None, T))

    assert sent, "the screen was redrawn"
    assert sent[0]["rich_message"] is not None
    assert sent[0]["reply_markup"] is not None, (
        "an edit with no markup is read as 'take the keyboard away'")


# --- the tail must not eat the screen ----------------------------------------
#
# Found by finishing a review that had been cut short: fourteen of its findings
# never reached a verifier, and this was among them. The cancelled tail was
# built before the loop and weighed against the budget on every candidate, but
# was never itself capped.


def _many(active: int, cancelled: int) -> list[dict]:
    return ([_order(i) for i in range(1, active + 1)]
            + [_order(i, status_group_id=6, status_name="canceled")
               for i in range(active + 1, active + cancelled + 1)])


def test_a_long_cancelled_tail_does_not_crowd_out_the_real_orders():
    """Measured before the fix: ten active orders and 689 cancelled produced a
    screen with nought sections, "Показано 1–0 з 10", and 694 blocks against a
    ceiling of 500 — so Telegram refused it and the plain screen came back."""
    blocks = rich_orders_blocks(_many(10, 689), T, cancelled=True)
    assert len(_sections(blocks)) == 10, "every active order still has its place"
    assert rich.fits(blocks), "and the screen is one Telegram will take"


def test_the_tail_says_how_many_it_left_out():
    blocks = rich_orders_blocks(_many(2, 400), T, cancelled=True)
    said = " ".join(str(getattr(b, "text", "")) for b in blocks)
    assert "…та інші" in said or "та інші" in said


def test_the_screen_never_says_it_is_showing_one_to_nought():
    """A range that starts after it ends is not a smaller screen, it is a
    broken one."""
    for cancelled_count in (0, 5, 400, 689):
        blocks = rich_orders_blocks(_many(10, cancelled_count), T, cancelled=True)
        said = " ".join(str(getattr(b, "text", "")) for b in blocks)
        assert "1–0" not in said, f"with {cancelled_count} cancelled"


def test_at_least_one_order_survives_any_budget():
    """A list of orders with no orders on it says nothing at all; a refusal we
    fall back from is the better failure."""
    blocks = rich_orders_blocks(_many(1, 2000), T, cancelled=True)
    assert len(_sections(blocks)) >= 1


# --- what the redraw is for --------------------------------------------------

def test_the_parcel_answer_lands_in_a_section_she_can_see():
    """is_open was pinned to the newest order, so the answer to "Де посилка?"
    arrived inside a folded section: she tapped, the screen redrew, and nothing
    she could see had changed."""
    rows = [_order(1), _order(2), _order(3)]

    quiet = [b for b in rich_orders_blocks(rows, T) if b.type == "details"]
    assert quiet[0].is_open and not quiet[2].is_open, "the newest, by default"

    answered = [b for b in rich_orders_blocks(
        rows, T, parcels={3: ["Статус: В дорозі"]}) if b.type == "details"]
    assert answered[2].is_open, "the one being answered about"


def test_asking_about_one_parcel_does_not_fold_what_she_was_reading():
    """The half the test above used to assert the wrong way round.

    It ended with `assert not answered[0].is_open` — pinning the newest order
    shut the moment another was asked about, because one expression did two
    jobs: `in parcels if parcels else row is active[0]` chose who got carrier
    lines AND closed everything else. Measured on three orders: tap «Де
    посилка?» on the third and the first, open until that moment, folded. She
    got her answer and lost her place, and the more orders she had the more it
    cost.

    The two reasons a section is open add up. They do not replace each other."""
    rows = [_order(1), _order(2), _order(3)]

    answered = [b for b in rich_orders_blocks(
        rows, T, parcels={3: ["Статус: В дорозі"]}) if b.type == "details"]

    assert answered[2].is_open, "the one being answered about"
    assert answered[0].is_open, (
        "the newest folded when another order was asked about")
    assert not answered[1].is_open, (
        "and nothing else opened: two reasons, not a free-for-all")


def test_the_stale_warning_reaches_the_rich_screen_too():
    """§5.5 is the customer's half of the stalled-sync alert. The plain screen
    has carried it since it was written; the rich one dropped it, so it was
    dead for everyone modern enough to see blocks."""
    blocks = rich_orders_blocks([_order(1)], T, notice="⏳ Дані застаріли")
    said = [str(getattr(b, "text", "")) for b in blocks]
    assert any("застаріли" in line for line in said)
    # Above the list: a long history must not bury it.
    assert said.index(next(l for l in said if "застаріли" in l)) <= 1


# --- the entrance the migration never tested ---------------------------------
#
# Every test above starts from a rich anchor, and that is precisely how the
# defect survived them. The menu message is plain; with the bottom keyboard off
# it is the *only* way into the orders screen — and `render` dropped the blocks
# whenever the anchor was plain, a rule left behind by an admin gate that had
# already been removed. Six blocks built, six thrown away, nothing logged, and
# the owner looking at the same screen as a week before.


def _plain_anchor(monkeypatch):
    """The menu message: a real Message with no `rich_message`."""
    from aiogram.types import Message as _M

    sent: list[dict] = []
    msg = _M.model_validate({
        "message_id": 11, "date": 0, "chat": {"id": CHAT, "type": "private"},
        "text": "Обери, що цікавить",
    })

    async def fake_edit(self, text=None, reply_markup=None, **kw):
        sent.append({"text": text, "rich_message": kw.get("rich_message"),
                     "reply_markup": reply_markup})
        return self

    monkeypatch.setattr(_M, "edit_text", fake_edit)
    return msg, sent


class _NoState:
    async def clear(self) -> None:
        return None

    async def get_data(self) -> dict:
        return {}

    async def update_data(self, **kwargs) -> dict:
        return dict(kwargs)

    async def set_state(self, state) -> None:
        return None


def test_the_menu_opens_the_orders_screen_rich(monkeypatch, db_with_orders):
    """Tapping 📦 in the menu must draw blocks over the plain menu message.

    Editing a plain message into a rich one is allowed — verified against the
    live API, `docs/rich-messages.md`. Refusing to do it is what made the whole
    migration invisible in production.
    """
    from bot.handlers import menu as mod

    msg, sent = _plain_anchor(monkeypatch)
    asyncio.run(mod.orders_from_menu(
        _callback(msg, "menu:open_orders"), _NoState(), None, None, None, T))

    assert sent, "the menu message was redrawn"
    assert sent[0]["rich_message"] is not None, (
        "the orders screen arrived plain: render threw the blocks away because "
        "the menu it replaced was plain")
    assert sent[0]["text"] is None


# --- the slab must describe the screen it sits under -------------------------
#
# Found by auditing the fix above rather than the code it fixed. Once blocks
# won everywhere, `rich=_anchor_is_rich(callback)` in show_order and
# track_parcel was asking a question nobody answers any more: the blocks went
# out unconditionally, the keyboard was chosen by the anchor, and a tap from a
# plain menu produced a rich screen wearing the plain screen's slab — every
# per-order date button drawn twice, once in the slab and once in its own
# section.


def test_a_redraw_from_a_plain_anchor_wears_the_rich_slab(monkeypatch, db_with_orders):
    from bot.callbacks import OrderAction
    from bot.handlers import orders as mod

    msg, sent = _plain_anchor(monkeypatch)
    asyncio.run(mod.show_order(
        _callback(msg, "ord:show:2:0:"),
        OrderAction(action="show", order_id=2, page=0, state=""),
        None, T))

    assert sent[0]["rich_message"] is not None, "the screen went out as blocks"
    labels = [b.text for row in sent[0]["reply_markup"].inline_keyboard
              for b in row]
    dated = [x for x in labels if "." in x and any(c.isdigit() for c in x)]
    assert not dated, (
        "the slab carries per-order buttons that the blocks already carry: "
        f"{dated} — the keyboard was picked for a screen that did not arrive")


def test_asking_where_the_parcel_is_from_a_plain_anchor_wears_the_rich_slab(
        monkeypatch, db_with_orders):
    from bot.callbacks import OrderAction
    from bot.handlers import orders as mod

    msg, sent = _plain_anchor(monkeypatch)
    asyncio.run(mod.track_parcel(
        _callback(msg, "ord:track:1:0:"),
        OrderAction(action="track", order_id=1, page=0, state=""),
        None, T))

    assert sent[0]["rich_message"] is not None
    labels = [b.text for row in sent[0]["reply_markup"].inline_keyboard
              for b in row]
    dated = [x for x in labels if "." in x and any(c.isdigit() for c in x)]
    assert not dated, f"plain slab under a rich screen: {dated}"


# --- a trimmed screen says so, whichever thing trimmed it --------------------


def _plain_history(n: int) -> list[dict]:
    return [{"id": i, "order_name": f"#{i}", "status_name": "Прибув",
             "status_group_id": 4, "grand_total": 100, "currency": "грн",
             "ordered_at": f"2026-08-{(i % 28) + 1:02d}T10:00:00",
             "products_json": "[]", "tracking_code": "",
             "delivery_city": "", "receive_point": ""} for i in range(1, n + 1)]


def _paragraphs(blocks) -> list[str]:
    said = []
    for b in blocks:
        text = getattr(b, "text", None)
        if isinstance(text, str):
            said.append(text)
    return said


def test_a_later_page_says_which_orders_it_is_showing():
    """`active` was sliced before the count that decides this line, so `shown`
    always equalled the total and the line never appeared: page 1 showed ten of
    twenty-five orders and claimed nothing about the other fifteen."""
    rows = _plain_history(25)
    said = " ".join(_paragraphs(rich_orders_blocks(rows, T, page=1)))
    assert "11" in said and "20" in said and "25" in said, (
        f"a paged screen must say what it is showing, got: {said[:200]}")


def test_the_first_page_of_a_short_history_says_nothing():
    """The line is for a trimmed screen. Saying "1–3 з 3" on a complete one is
    noise, and the reservation for it must be given back."""
    said = " ".join(_paragraphs(rich_orders_blocks(_plain_history(3), T)))
    assert "з 3" not in said and "of 3" not in said


# --- the stalled-sync warning survives a redraw ------------------------------
#
# §5.5 exists for the window where the cache is hours old. It was computed once
# at the entrance and erased by the next redraw — including `_fill_in_parcel`,
# which fires a second later on its own, so the warning could vanish from a
# screen the customer never touched.


def test_a_redraw_keeps_the_stalled_sync_warning(monkeypatch, db_with_orders):
    from bot.callbacks import OrderAction
    from bot.handlers import orders as mod

    async def stale(chat_id, t, **kw):
        return "⏳ Дані застаріли"

    monkeypatch.setattr(mod, "stale_notice", stale)
    msg, sent = _rich_anchor(monkeypatch)
    asyncio.run(mod.show_order(
        _callback(msg, "ord:show:2:0:"),
        OrderAction(action="show", order_id=2, page=0, state=""),
        None, T))

    said = " ".join(_paragraphs(sent[0]["rich_message"].blocks))
    assert "застаріли" in said, (
        "the redraw dropped the warning the entrance had put there")


# --- a heading is a heading, on every screen ---------------------------------


def test_the_favourites_screen_opens_with_a_real_heading():
    """The orders screen has had this test since it was migrated. Favourites
    did not, and shipped `<b>` to the customer as four characters: block text
    is structured, not parsed."""
    from bot.handlers.orders import rich_favourites_blocks

    blocks = rich_favourites_blocks([], {}, {}, set(), T, "https://shop.example")
    head = blocks[0].text
    assert "<b>" not in str(head) and "</b>" not in str(head), (
        f"markup in a block heading reaches the customer literally: {head!r}")


def test_a_screen_with_no_blocks_never_asks_telegram_for_a_rich_one():
    """`rich.send(..., screen.blocks or [], ...)` is how four call sites spell
    "send this screen", and the screens with no rich form — no phone, no
    orders, no favourites — arrive as an empty list. An empty rich message is a
    refusal, and the refusal branch alerts the admins that the migration may be
    dead. Burning that alert on the empty screens mutes the only detector the
    migration has."""
    bot = _Bot()
    asyncio.run(rich.send(bot, CHAT, [], plain="Поки що нема замовлень"))

    assert not bot.rich, "an empty screen must not be sent as a rich message"
    assert bot.plain == [(CHAT, "Поки що нема замовлень", None)]


# --- one screen, one of each button ------------------------------------------


def _slab_and_blocks(rows, **kw):
    from bot.handlers.orders import _orders_kb
    slab = [b.text for row in _orders_kb(rows, T, rich=True, **kw).inline_keyboard
            for b in row]
    blocks = [btn.text for b in rich_orders_blocks(rows, T, **kw)
              for btn in (getattr(b, "buttons", None) or [])]
    return slab, blocks


def test_no_button_appears_both_in_the_slab_and_in_the_blocks(db_with_orders):
    """«📋 Меню» was on the rich screen twice — once under the message and once
    at the end of the blocks.

    Written as the general rule rather than about that one button, because it
    is the third time this class has shown up: the per-order date buttons were
    drawn twice for the same reason and were closed with the same flag, and the
    menu key was simply missed. The slab under a rich screen carries what the
    blocks cannot, and nothing else."""
    rows = [{"id": i, "order_name": f"#{i}", "status_name": "Прибув",
             "status_group_id": 4, "grand_total": 100, "currency": "грн",
             "ordered_at": f"2026-08-{i + 1:02d}T10:00:00", "products_json": "[]",
             "tracking_code": "", "delivery_city": "", "receive_point": ""}
            for i in (1, 2, 3)]
    slab, blocks = _slab_and_blocks(rows)

    both = set(slab) & set(blocks)
    assert not both, f"drawn twice on one screen: {sorted(both)}"


def test_the_rich_screen_still_has_a_way_out(db_with_orders):
    """Dropping the duplicate must not drop the only one. On the rich screen
    the way out lives in the blocks, which is why the slab may lose it."""
    rows = [{"id": 1, "order_name": "#1", "status_name": "Прибув",
             "status_group_id": 4, "grand_total": 100, "currency": "грн",
             "ordered_at": "2026-08-02T10:00:00", "products_json": "[]",
             "tracking_code": "", "delivery_city": "", "receive_point": ""}]
    slab, blocks = _slab_and_blocks(rows)
    assert T.BTN_MENU in slab + blocks, "the rich screen lost its way out"


def test_the_plain_screen_keeps_its_own(db_with_orders):
    """It has no blocks to carry one, so the slab must."""
    from bot.handlers.orders import _orders_kb

    rows = [{"id": 1, "order_name": "#1", "status_name": "Прибув",
             "status_group_id": 4, "grand_total": 100, "currency": "грн",
             "ordered_at": "2026-08-02T10:00:00", "products_json": "[]",
             "tracking_code": "", "delivery_city": "", "receive_point": ""}]
    slab = [b.text for row in _orders_kb(rows, T).inline_keyboard for b in row]
    assert T.BTN_MENU in slab
