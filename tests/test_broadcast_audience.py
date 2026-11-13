"""Choosing who gets a broadcast: «everyone», «only one purchase», «more than two».

Three layers, each where it decides something:

* core/domain/audience.py — the rule. Pure, so every boundary is pinned here
  with dates and counts written out rather than discovered.
* core/repos/users.py — what a profile is made of. This is where "a purchase"
  is defined, and where a cancelled order or a demo order would quietly turn
  a one-time buyer into a regular if the definition drifted.
* the picker in bot/handlers/broadcast.py — that a tap moves one filter, the
  count on the send button is the number queued, and the queue gets exactly
  the people the screen promised.
"""
from __future__ import annotations

import asyncio
import json
from urllib.parse import parse_qs, urlparse
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.methods import (AnswerCallbackQuery, EditMessageReplyMarkup,
                             EditMessageText, SendMessage)

import broadcast_fakes as fakes
from bot.callbacks import BroadcastAction, BroadcastFilter, PromoClaim
from bot.handlers import broadcast as mod
from bot.states import BroadcastStates
from core.domain.audience import Audience, Profile, Purchase, matches
from core.domain.promo import Promo
from core.domain.quiet import SHOP_TZ
from core.ports.discounts import DiscountLookupFailed
from core.i18n import Texts
from core.repos import base as repos_base
from core.repos.broadcast import get_unfinished_broadcasts
from core.repos.events import log_event
from core.repos.outbox import claim
from core.repos.schema import init_db
from core.repos.users import SqliteMailingList, opt_out_user, save_user

NOW = datetime(2026, 11, 12, 12, 0, tzinfo=timezone.utc)
UK = Texts("uk")


def _bought(*days_ago: int, products=("COSRX Advanced Snail 96 Mucin",)) -> tuple:
    return tuple(Purchase(at=NOW - timedelta(days=d), products=products)
                 for d in days_ago)


def _who(audience: Audience, **profiles: Profile) -> set[str]:
    return {name for name, profile in profiles.items()
            if matches(audience, profile, NOW)}


# --- the rule -------------------------------------------------------------------

def test_purchase_counts_say_what_the_owner_asked_for():
    people = {f"bought_{n}": Profile(chat_id=n, purchases=_bought(*[1] * n))
              for n in (0, 1, 2, 3, 5)}
    assert _who(Audience(), **people) == set(people)
    assert _who(Audience(purchases="none"), **people) == {"bought_0"}
    assert _who(Audience(purchases="1"), **people) == {"bought_1"}
    assert _who(Audience(purchases="2+"), **people) == {"bought_2", "bought_3", "bought_5"}
    # «більше двох» — more than two
    assert _who(Audience(purchases="3+"), **people) == {"bought_3", "bought_5"}
    assert _who(Audience(purchases="5+"), **people) == {"bought_5"}


def test_the_latest_purchase_decides_how_long_ago():
    people = {
        "recent": Profile(1, purchases=_bought(200, 10)),
        "lapsing": Profile(2, purchases=_bought(60)),
        "gone": Profile(3, purchases=_bought(400, 120)),
        "never": Profile(4),
    }
    assert _who(Audience(last_order="30"), **people) == {"recent"}
    assert _who(Audience(last_order="30-90"), **people) == {"lapsing"}
    assert _who(Audience(last_order="90+"), **people) == {"gone"}


def test_a_product_word_matches_in_any_case_and_any_alphabet():
    serum = ("LALARECIPE Yuzu Vita C Ampoule освітлююча СИРОВАТКА",)
    people = {"serum": Profile(1, purchases=_bought(5, products=serum)),
              "snail": Profile(2, purchases=_bought(5))}
    assert _who(Audience(bought="сироватка"), **people) == {"serum"}
    assert _who(Audience(bought="cosrx"), **people) == {"snail"}
    assert _who(Audience(bought="neogen"), **people) == set()


def test_waiting_for_a_restock_with_and_without_a_word():
    people = {"mask": Profile(1, waiting_for=("NEOGEN Маска з екзосомами",)),
              "nothing": Profile(2)}
    assert _who(Audience(waiting="yes"), **people) == {"mask"}
    assert _who(Audience(waiting="yes", waiting_for="маска"), **people) == {"mask"}
    assert _who(Audience(waiting="yes", waiting_for="крем"), **people) == set()


def test_new_in_the_bot_and_away_from_it():
    people = {
        "new": Profile(1, joined_at=NOW - timedelta(days=3), last_seen=NOW),
        "month": Profile(2, joined_at=NOW - timedelta(days=20), last_seen=NOW),
        "away": Profile(3, joined_at=NOW - timedelta(days=200),
                        last_seen=NOW - timedelta(days=45)),
        "silent": Profile(4, joined_at=NOW - timedelta(days=200)),
    }
    assert _who(Audience(activity="new7"), **people) == {"new"}
    assert _who(Audience(activity="new30"), **people) == {"new", "month"}
    assert _who(Audience(activity="idle30"), **people) == {"away", "silent"}


def test_conditions_combine_with_and():
    people = {
        "regular_lapsing": Profile(1, purchases=_bought(50, 70, 100)),
        "regular_recent": Profile(2, purchases=_bought(5, 70, 100)),
        "one_lapsing": Profile(3, purchases=_bought(50)),
    }
    audience = Audience(purchases="2+", last_order="30-90")
    assert _who(audience, **people) == {"regular_lapsing"}


def test_a_tap_steps_through_the_choices_and_comes_back():
    audience = Audience()
    seen = []
    for _ in range(7):
        audience = audience.stepped("purchases")
        seen.append(audience.purchases)
    assert seen == ["none", "1", "2+", "3+", "5+", "any", "none"]


def test_stored_state_that_no_longer_makes_sense_reads_as_no_filter():
    assert Audience.from_dict(None) == Audience()
    assert Audience.from_dict({"purchases": "7+", "activity": 3,
                               "bought": "  cosrx  "}) == Audience(bought="cosrx")


# --- what a profile is made of ---------------------------------------------------

@pytest.fixture()
def db(tmp_path, monkeypatch):
    path = tmp_path / "bot_data.db"
    monkeypatch.setattr(repos_base, "DB_PATH", str(path))
    asyncio.run(init_db())
    return path


def _order(path, chat_id: int, n: int, *, group: int = 4, source: str = "keycrm",
           at: str = "2026-11-01T08:43:56.000000Z",
           products=("COSRX Advanced Snail",)) -> None:
    with sqlite3.connect(path) as conn:
        conn.execute(
            "INSERT INTO orders (chat_id, source, source_order_id, merge_key, "
            "status_group_id, ordered_at, products_json) VALUES (?,?,?,?,?,?,?)",
            (chat_id, source, f"{chat_id}-{n}", f"m-{chat_id}-{n}", group, at,
             json.dumps([{"name": p, "qty": 1} for p in products],
                        ensure_ascii=False)))


def _people(path, *chat_ids: int) -> None:
    for chat_id in chat_ids:
        asyncio.run(save_user(chat_id, f"+38067{chat_id:07d}"))


def _recipients(audience: Audience | None = None) -> set[int]:
    return set(asyncio.run(SqliteMailingList().recipients(audience)))


def test_a_cancelled_order_and_a_demo_order_are_not_purchases(db):
    _people(db, 1, 2)
    _order(db, 1, 1)
    _order(db, 1, 2, group=6)              # cancelled
    _order(db, 1, 3, source="demo")        # /demo
    _order(db, 2, 1)
    _order(db, 2, 2)

    assert _recipients(Audience(purchases="1")) == {1}
    assert _recipients(Audience(purchases="2+")) == {2}


def test_every_timestamp_spelling_in_the_table_is_understood(db):
    _people(db, 1, 2, 3)
    _order(db, 1, 1, at="2026-11-01T08:43:56.000000Z")          # KeyCRM
    _order(db, 2, 1, at="2026-10-30T10:00:00+02:00")            # Shopify
    _order(db, 3, 1, at="")                                     # unknown

    recent = Audience(last_order="30")
    # The window is measured from today, so the fixtures are only "recent" for
    # a while; what must hold forever is that the unknown date never matches.
    assert 3 not in _recipients(recent)
    assert _recipients(Audience(purchases="1")) == {1, 2, 3}


def test_opted_out_people_are_never_in_a_filtered_audience(db):
    _people(db, 1, 2)
    _order(db, 1, 1)
    _order(db, 2, 1)
    asyncio.run(opt_out_user(2))

    assert _recipients(Audience(purchases="1")) == {1}
    assert _recipients() == {1}


def test_last_seen_is_what_the_person_did_not_what_the_bot_logged(db):
    _people(db, 1, 2)
    asyncio.run(log_event(1, "tap"))
    asyncio.run(log_event(None, "webhook_arrived"))   # the bot's own, no chat

    assert _recipients(Audience(activity="idle30")) == {2}


def test_waiting_comes_from_restock_subscriptions(db):
    _people(db, 1, 2)
    with sqlite3.connect(db) as conn:
        conn.execute("INSERT INTO stock_subscriptions (chat_id, sku, name) "
                     "VALUES (1, 'sku-1', 'NEOGEN Маска')")

    assert _recipients(Audience(waiting="yes")) == {1}
    assert _recipients(Audience(waiting="yes", waiting_for="маска")) == {1}


# --- the picker -----------------------------------------------------------------

STAFF = 1772368873


@pytest.fixture()
def shop(db):
    """Three customers: never bought, bought once, bought three times."""
    _people(db, 10, 11, 12)
    _order(db, 11, 1)
    for n in range(3):
        _order(db, 12, n, products=("NEOGEN Exosome Mask",))
    return db


ADMIN = 4242


def _config():
    return fakes.config(admins=[ADMIN], staff=[STAFF])


def _at_picker(bot) -> fakes.State:
    state = fakes.State()
    state.state = BroadcastStates.waiting_message.state
    asyncio.run(mod.process_broadcast_message(
        fakes.message(bot, STAFF, text="Знижка 20%"), _config(), state, None))
    assert state.state == BroadcastStates.waiting_confirm.state
    return state


def _tap(bot, state, data) -> None:
    handler = (mod.process_broadcast_filter if isinstance(data, BroadcastFilter)
               else mod.process_broadcast_name_action
               if state.state == BroadcastStates.waiting_name.state
               else mod.process_broadcast_confirm)
    asyncio.run(handler(fakes.callback(bot, STAFF, data.pack()), data,
                        _config(), state, None))


def _name(bot, state, text) -> None:
    asyncio.run(mod.process_broadcast_name(
        fakes.message(bot, STAFF, text=text), _config(), state, None))


def _send(bot, state, name="nov-2026-test") -> None:
    """«Send», then the name: the only way a broadcast goes out."""
    _tap(bot, state, BroadcastAction(action="send"))
    _name(bot, state, name)


def _screen(bot) -> EditMessageText:
    return [c for c in bot.calls if isinstance(c, EditMessageText)][-1]


def _buttons(markup) -> list[str]:
    return [row[0].text for row in markup.inline_keyboard]


def test_the_picker_opens_on_everyone(shop):
    bot = fakes.RecordingBot()
    _at_picker(bot)

    screen = bot.to(STAFF)[-1]
    assert screen.text == UK.MSG_BROADCAST_AUDIENCE.format(count=3, total=3)
    assert UK.BTN_BROADCAST_SEND.format(count=3) in _buttons(screen.reply_markup)
    assert UK.BTN_BROADCAST_RESET not in _buttons(screen.reply_markup)


def test_tapping_purchases_narrows_the_count_on_the_send_button(shop):
    bot = fakes.RecordingBot()
    state = _at_picker(bot)

    _tap(bot, state, BroadcastFilter(name="purchases"))   # → без покупок
    _tap(bot, state, BroadcastFilter(name="purchases"))   # → рівно 1
    screen = _screen(bot)
    assert screen.text == UK.MSG_BROADCAST_AUDIENCE.format(count=1, total=3)
    buttons = _buttons(screen.reply_markup)
    assert UK.BTN_AUD_PURCHASES.format(value=UK.AUD_PURCHASES_ONE) in buttons
    assert UK.BTN_BROADCAST_SEND.format(count=1) in buttons


def test_sending_queues_exactly_who_the_screen_promised(shop):
    bot = fakes.RecordingBot()
    state = _at_picker(bot)
    for _ in range(4):                                     # → 3 і більше
        _tap(bot, state, BroadcastFilter(name="purchases"))
    _send(bot, state)

    assert [row["chat_id"] for row in asyncio.run(claim(50))] == [12]
    assert state.state is None


def test_a_word_filter_asks_then_comes_back_with_the_word_on_it(shop):
    bot = fakes.RecordingBot()
    state = _at_picker(bot)

    _tap(bot, state, BroadcastFilter(name="bought"))
    assert _screen(bot).text == UK.MSG_BROADCAST_ASK_BOUGHT
    assert state.state == BroadcastStates.waiting_word.state

    asyncio.run(mod.process_broadcast_word(
        fakes.message(bot, STAFF, text="neogen"), _config(), state, None))
    screen = bot.to(STAFF)[-1]
    assert screen.text == UK.MSG_BROADCAST_AUDIENCE.format(count=1, total=3)
    assert UK.BTN_AUD_BOUGHT.format(value="«neogen»") in _buttons(screen.reply_markup)
    assert state.state == BroadcastStates.waiting_confirm.state

    _tap(bot, state, BroadcastFilter(name="bought"))       # tapped again: cleared
    assert UK.BTN_AUD_BOUGHT.format(value=UK.AUD_BOUGHT_ANY) in _buttons(
        _screen(bot).reply_markup)


def test_a_command_is_not_taken_for_the_word(shop):
    bot = fakes.RecordingBot()
    state = _at_picker(bot)
    _tap(bot, state, BroadcastFilter(name="bought"))
    asyncio.run(mod.process_broadcast_word(
        fakes.message(bot, STAFF, text="/stats"), _config(), state, None))

    assert bot.texts_to(STAFF)[-1] == UK.MSG_BROADCAST_ASK_BOUGHT
    assert state.state == BroadcastStates.waiting_word.state


def test_nobody_left_means_no_send_button_and_reset_brings_everyone_back(shop):
    bot = fakes.RecordingBot()
    state = _at_picker(bot)
    for _ in range(5):                                     # → 5 і більше
        _tap(bot, state, BroadcastFilter(name="purchases"))

    screen = _screen(bot)
    assert screen.text == UK.MSG_BROADCAST_AUDIENCE_EMPTY.format(total=3)
    assert not any(b.startswith("✅") for b in _buttons(screen.reply_markup))

    _tap(bot, state, BroadcastAction(action="reset"))
    assert _screen(bot).text == UK.MSG_BROADCAST_AUDIENCE.format(count=3, total=3)


def test_a_stale_send_button_with_nobody_behind_it_queues_nothing(shop):
    bot = fakes.RecordingBot()
    state = _at_picker(bot)
    _tap(bot, state, BroadcastFilter(name="purchases"))   # → без покупок: chat 10
    asyncio.run(opt_out_user(10))                          # …who then leaves
    _tap(bot, state, BroadcastAction(action="send"))

    assert asyncio.run(claim(50)) == []
    assert state.state == BroadcastStates.waiting_confirm.state
    assert _screen(bot).text == UK.MSG_BROADCAST_AUDIENCE_EMPTY.format(total=2)


# --- a test send to the team ---------------------------------------------------

def test_the_team_is_admins_and_staff_once_each():
    assert mod._team(fakes.config(admins=[1, 2], staff=[2, 3])) == [1, 2, 3]


def test_the_picker_offers_a_test_to_the_team(shop):
    bot = fakes.RecordingBot()
    _at_picker(bot)

    assert UK.BTN_BROADCAST_TEST.format(count=2) in _buttons(bot.to(STAFF)[-1].reply_markup)


def test_a_test_goes_to_the_team_and_to_no_customer(shop):
    bot = fakes.RecordingBot()
    state = _at_picker(bot)
    for _ in range(4):                                     # → 3 і більше: chat 12
        _tap(bot, state, BroadcastFilter(name="purchases"))
    before = len(bot.calls)

    _tap(bot, state, BroadcastAction(action="test"))

    new = bot.calls[before:]
    copies = [c for c in new if isinstance(c, SendMessage) and c.text == "Знижка 20%"]
    # The keyboard is gone before the first copy, so a second tap cannot
    # send them all again.
    first_edit = next(c for c in new if isinstance(c, EditMessageText))
    assert first_edit.text == UK.MSG_BROADCAST_TEST_SENDING
    assert first_edit.reply_markup is None
    assert new.index(first_edit) < new.index(copies[0])
    assert [c.chat_id for c in copies] == [ADMIN, STAFF]
    assert all(c.disable_notification is False for c in copies)
    assert not [c for c in bot.calls if getattr(c, "chat_id", None) in (10, 11, 12)]
    # Not a campaign: nothing queued, nothing in the journal.
    assert asyncio.run(claim(50)) == []
    assert asyncio.run(get_unfinished_broadcasts()) == []
    assert _screen(bot).text == UK.MSG_BROADCAST_TEST_SENT.format(sent=2, total=2)
    # A fresh picker under the copies, with the filters as they were, that
    # says the real send is still to come and how to stop at the test.
    picker = new[-1]
    assert isinstance(picker, SendMessage) and picker.chat_id == STAFF
    assert picker.text == (UK.MSG_BROADCAST_AFTER_TEST + "\n\n"
                           + UK.MSG_BROADCAST_AUDIENCE.format(count=1, total=3))
    assert state.state == BroadcastStates.waiting_confirm.state


def test_the_real_send_still_works_after_a_test(shop):
    bot = fakes.RecordingBot()
    state = _at_picker(bot)
    _tap(bot, state, BroadcastAction(action="test"))
    _send(bot, state)

    assert sorted(row["chat_id"] for row in asyncio.run(claim(50))) == [10, 11, 12]


def test_a_team_member_who_never_started_the_bot_is_counted_out(shop):
    never_started = TelegramForbiddenError(
        method=None, message="Forbidden: bot can't initiate conversation with a user")
    bot = fakes.RecordingBot(refuse={ADMIN: never_started})
    state = _at_picker(bot)

    _tap(bot, state, BroadcastAction(action="test"))

    assert _screen(bot).text == (UK.MSG_BROADCAST_TEST_SENT.format(sent=1, total=2)
                                 + "\n" + UK.MSG_BROADCAST_TEST_MISSED)
    assert state.state == BroadcastStates.waiting_confirm.state


# --- link buttons under the post ------------------------------------------------

def _type(bot, state, text) -> None:
    asyncio.run(mod.process_broadcast_buttons(
        fakes.message(bot, STAFF, text=text), _config(), state, None))


def _last_to_staff(bot) -> SendMessage:
    return [c for c in bot.to(STAFF) if isinstance(c, SendMessage)][-1]


def test_the_picker_has_a_buttons_row_saying_there_are_none(shop):
    bot = fakes.RecordingBot()
    _at_picker(bot)

    label = UK.BTN_BROADCAST_BUTTONS.format(value=UK.BROADCAST_BUTTONS_NONE)
    assert label in _buttons(bot.to(STAFF)[-1].reply_markup)


def test_buttons_are_asked_for_shown_on_the_post_and_queued_with_it(shop):
    bot = fakes.RecordingBot()
    state = _at_picker(bot)

    _tap(bot, state, BroadcastAction(action="buttons"))
    assert _screen(bot).text == UK.MSG_BROADCAST_ASK_BUTTONS
    assert state.state == BroadcastStates.waiting_buttons.state

    before = len(bot.calls)
    _type(bot, state, "Каталог - https://ks.example/catalog")
    preview, picker = [c for c in bot.calls[before:] if getattr(c, "chat_id", None) == STAFF]
    # The post again, as it will go: with the button under it.
    assert preview.text == "Знижка 20%"
    assert preview.reply_markup.inline_keyboard[0][0].url == "https://ks.example/catalog"
    assert UK.BTN_BROADCAST_BUTTONS.format(value=1) in _buttons(picker.reply_markup)
    assert state.state == BroadcastStates.waiting_confirm.state

    _send(bot, state)
    payloads = [json.loads(row["payload"]) for row in asyncio.run(claim(50))]
    assert len(payloads) == 3
    assert all(p["post"]["buttons"] == [[{"text": "Каталог",
                                          "url": "https://ks.example/catalog"}]]
               for p in payloads)


def test_a_line_that_is_not_a_button_is_quoted_back_and_the_question_stays(shop):
    bot = fakes.RecordingBot()
    state = _at_picker(bot)
    _tap(bot, state, BroadcastAction(action="buttons"))

    _type(bot, state, "Знижка <20%>")

    assert _last_to_staff(bot).text == UK.MSG_BROADCAST_BUTTONS_INVALID.format(
        line="Знижка &lt;20%&gt;")
    assert state.state == BroadcastStates.waiting_buttons.state


def test_a_dash_takes_the_buttons_away(shop):
    bot = fakes.RecordingBot()
    state = _at_picker(bot)
    _tap(bot, state, BroadcastAction(action="buttons"))
    _type(bot, state, "Каталог - https://ks.example")
    _tap(bot, state, BroadcastAction(action="buttons"))

    _type(bot, state, "-")

    assert "buttons" not in state.data["broadcast_post"]
    label = UK.BTN_BROADCAST_BUTTONS.format(value=UK.BROADCAST_BUTTONS_NONE)
    assert label in _buttons(_last_to_staff(bot).reply_markup)


def test_buttons_telegram_refuses_are_not_kept(shop):
    bot = fakes.RecordingBot()
    state = _at_picker(bot)
    _tap(bot, state, BroadcastAction(action="buttons"))
    bot._raises = [TelegramBadRequest(method=None,
                                      message="Bad Request: BUTTON_URL_INVALID")]

    _type(bot, state, "Каталог - https://ks.example")

    assert _last_to_staff(bot).text == UK.MSG_BROADCAST_BUTTONS_REFUSED
    assert "buttons" not in state.data["broadcast_post"]
    assert state.state == BroadcastStates.waiting_buttons.state


def test_an_album_is_told_why_it_cannot_have_buttons(shop):
    bot = fakes.RecordingBot()
    state = _at_picker(bot)
    state.data["broadcast_post"] = {"album": [{"photo": [{"file_id": "p"}]}]}

    _tap(bot, state, BroadcastAction(action="buttons"))

    [answer] = [c for c in bot.calls if isinstance(c, AnswerCallbackQuery)][-1:]
    assert answer.text == UK.MSG_BROADCAST_BUTTONS_ALBUM and answer.show_alert
    assert state.state == BroadcastStates.waiting_confirm.state


def test_a_bare_link_to_the_shop_is_answered_with_the_line_to_send(shop):
    bot = fakes.RecordingBot()
    state = _at_picker(bot)
    _tap(bot, state, BroadcastAction(action="buttons"))

    _type(bot, state, "https://koreanstory.com.ua/?a=1&b=2")

    link = "https://koreanstory.com.ua/?a=1&amp;b=2"
    assert _last_to_staff(bot).text == UK.MSG_BROADCAST_BUTTONS_NEED_TEXT.format(
        line=link, example=f"{UK.BROADCAST_BUTTON_EXAMPLE_LABEL} - {link}")
    assert state.state == BroadcastStates.waiting_buttons.state


def test_a_bare_instagram_link_becomes_an_instagram_button(shop):
    bot = fakes.RecordingBot()
    state = _at_picker(bot)
    _tap(bot, state, BroadcastAction(action="buttons"))

    _type(bot, state, "https://www.instagram.com/korean_story_official/")

    assert state.data["broadcast_post"]["buttons"] == [[{
        "text": "Instagram", "url": "https://www.instagram.com/korean_story_official/"}]]
    assert state.state == BroadcastStates.waiting_confirm.state


# --- the name that sends ---------------------------------------------------------

def test_the_send_button_alone_sends_nothing(shop):
    bot = fakes.RecordingBot()
    state = _at_picker(bot)

    _tap(bot, state, BroadcastAction(action="send"))

    assert asyncio.run(claim(50)) == []
    assert asyncio.run(get_unfinished_broadcasts()) == []
    assert state.state == BroadcastStates.waiting_name.state
    screen = _screen(bot)
    assert screen.text == UK.MSG_BROADCAST_ASK_NAME.format(count=3)
    # Only ways out under the question: the name is typed, never tapped.
    assert _buttons(screen.reply_markup) == [UK.BTN_BROADCAST_BACK,
                                            UK.BTN_BROADCAST_CANCEL]


def test_a_typed_name_sends_and_is_kept_with_the_job(shop):
    bot = fakes.RecordingBot()
    state = _at_picker(bot)

    _send(bot, state, name="Nov-2026-Company1")

    assert len(asyncio.run(claim(50))) == 3
    [job] = asyncio.run(get_unfinished_broadcasts())
    assert job["text"] == "[nov-2026-company1] Знижка 20%"
    assert _last_to_staff(bot).text == UK.MSG_BROADCAST_STARTED_NAMED.format(
        name="nov-2026-company1")
    assert state.state is None


@pytest.mark.parametrize("typed", ["cancel", "stop", "Stop", "-", "назад", "ні"])
def test_a_way_out_typed_as_a_name_goes_back_and_sends_nothing(shop, typed):
    """«stop» and «cancel» are perfectly good names by shape. Taken for one,
    the word a person types to get out would send the broadcast to everybody."""
    bot = fakes.RecordingBot()
    state = _at_picker(bot)
    _tap(bot, state, BroadcastAction(action="send"))

    _name(bot, state, typed)

    assert asyncio.run(claim(50)) == []
    assert state.state == BroadcastStates.waiting_confirm.state
    assert _last_to_staff(bot).text == UK.MSG_BROADCAST_AUDIENCE.format(count=3, total=3)


@pytest.mark.parametrize("typed", ["ok", "розсилка-листопад", "nov 2026", "<b>x</b>"])
def test_a_name_of_the_wrong_shape_is_explained_and_sends_nothing(shop, typed):
    bot = fakes.RecordingBot()
    state = _at_picker(bot)
    _tap(bot, state, BroadcastAction(action="send"))

    _name(bot, state, typed)

    assert asyncio.run(claim(50)) == []
    assert state.state == BroadcastStates.waiting_name.state
    assert _last_to_staff(bot).text == UK.MSG_BROADCAST_NAME_INVALID.format(
        name=typed.replace("<", "&lt;").replace(">", "&gt;"))


def test_back_returns_to_the_picker_with_the_filters_as_they_were(shop):
    bot = fakes.RecordingBot()
    state = _at_picker(bot)
    _tap(bot, state, BroadcastFilter(name="purchases"))    # → без покупок: chat 10
    _tap(bot, state, BroadcastAction(action="send"))

    _tap(bot, state, BroadcastAction(action="back"))

    assert state.state == BroadcastStates.waiting_confirm.state
    assert _screen(bot).text == UK.MSG_BROADCAST_AUDIENCE.format(count=1, total=3)
    assert asyncio.run(claim(50)) == []


def test_a_stale_send_under_the_name_question_neither_sends_nor_cancels(shop):
    bot = fakes.RecordingBot()
    state = _at_picker(bot)
    _tap(bot, state, BroadcastAction(action="send"))

    _tap(bot, state, BroadcastAction(action="send"))      # an older picker's button

    assert state.state == BroadcastStates.waiting_name.state
    assert asyncio.run(claim(50)) == []


@pytest.mark.parametrize("typed", ["так", "yes", "nov-2026-test", "щось інше"])
def test_typing_on_the_picker_sends_nothing_and_cancels_nothing(shop, typed):
    """«так» used to send from here, which would have made the name a formality."""
    bot = fakes.RecordingBot()
    state = _at_picker(bot)

    asyncio.run(mod.process_broadcast_confirm_text(
        fakes.message(bot, STAFF, text=typed), _config(), state, None))

    assert asyncio.run(claim(50)) == []
    assert state.state == BroadcastStates.waiting_confirm.state
    assert _last_to_staff(bot).text == UK.MSG_BROADCAST_USE_SEND


# --- a promo code under the post ------------------------------------------------

def _promo(bot, state, text) -> None:
    asyncio.run(mod.process_broadcast_promo(
        fakes.message(bot, STAFF, text=text), _config(), state, None))


def test_the_picker_has_a_promo_row_saying_there_is_none(shop):
    bot = fakes.RecordingBot()
    _at_picker(bot)

    label = UK.BTN_BROADCAST_PROMO.format(value=UK.BROADCAST_PROMO_NONE)
    assert label in _buttons(bot.to(STAFF)[-1].reply_markup)


def test_a_promo_is_asked_for_shown_on_the_post_and_queued_with_it(shop):
    bot = fakes.RecordingBot()
    state = _at_picker(bot)

    _tap(bot, state, BroadcastAction(action="promo"))
    assert _screen(bot).text == UK.MSG_BROADCAST_ASK_PROMO
    assert state.state == BroadcastStates.waiting_promo.state

    before = len(bot.calls)
    _promo(bot, state, "NOV20")
    preview, picker = [c for c in bot.calls[before:] if getattr(c, "chat_id", None) == STAFF]
    [[claim_button]] = preview.reply_markup.inline_keyboard
    assert (claim_button.text, claim_button.callback_data) == (UK.BTN_PROMO_CLAIM,
                                                              "promo:NOV20")
    assert UK.BTN_BROADCAST_PROMO.format(value="NOV20") in _buttons(picker.reply_markup)
    assert state.state == BroadcastStates.waiting_confirm.state

    _send(bot, state)
    payloads = [json.loads(row["payload"]) for row in asyncio.run(claim(50))]
    assert len(payloads) == 3
    assert all(p["post"]["promo"] == {"code": "NOV20", "text": UK.BTN_PROMO_CLAIM}
               for p in payloads)


def test_a_code_of_the_wrong_shape_is_quoted_back_and_the_question_stays(shop):
    bot = fakes.RecordingBot()
    state = _at_picker(bot)
    _tap(bot, state, BroadcastAction(action="promo"))

    _promo(bot, state, "<NOV 20>")

    assert _last_to_staff(bot).text == UK.MSG_BROADCAST_PROMO_INVALID.format(
        code="&lt;NOV 20&gt;")
    assert state.state == BroadcastStates.waiting_promo.state
    assert "promo" not in state.data["broadcast_post"]


def test_a_dash_takes_the_promo_away(shop):
    bot = fakes.RecordingBot()
    state = _at_picker(bot)
    _tap(bot, state, BroadcastAction(action="promo"))
    _promo(bot, state, "NOV20")
    _tap(bot, state, BroadcastAction(action="promo"))

    _promo(bot, state, "-")

    assert "promo" not in state.data["broadcast_post"]
    label = UK.BTN_BROADCAST_PROMO.format(value=UK.BROADCAST_PROMO_NONE)
    assert label in _buttons(_last_to_staff(bot).reply_markup)


def test_an_album_is_told_why_it_cannot_have_a_promo(shop):
    bot = fakes.RecordingBot()
    state = _at_picker(bot)
    state.data["broadcast_post"] = {"album": [{"photo": [{"file_id": "p"}]}]}

    _tap(bot, state, BroadcastAction(action="promo"))

    [answer] = [c for c in bot.calls if isinstance(c, AnswerCallbackQuery)][-1:]
    assert answer.text == UK.MSG_BROADCAST_BUTTONS_ALBUM and answer.show_alert
    assert state.state == BroadcastStates.waiting_confirm.state


CUSTOMER = 11


def _claim(bot, monkeypatch, code: str) -> list:
    tracked = []
    monkeypatch.setattr(mod, "track", lambda *a, **kw: tracked.append((a, kw)))
    data = PromoClaim(code=code)
    asyncio.run(mod.claim_promo(fakes.callback(bot, CUSTOMER, data.pack()), data,
                                _config(), UK))
    return tracked


def test_a_customer_who_taps_gets_the_code_and_the_shop_with_it_applied(shop, monkeypatch):
    bot = fakes.RecordingBot()

    tracked = _claim(bot, monkeypatch, "NOV20")

    [message] = [c for c in bot.to(CUSTOMER) if isinstance(c, SendMessage)]
    assert message.text == UK.MSG_PROMO_CODE.format(code="NOV20")
    [[shop_button]] = message.reply_markup.inline_keyboard
    assert shop_button.text == UK.BTN_PROMO_SHOP
    link = urlparse(shop_button.url)
    assert link.path == "/discount/NOV20"
    landing = parse_qs(link.query)["redirect"][0]
    assert parse_qs(urlparse(landing).query)["utm_campaign"] == ["broadcast_promo"]
    assert tracked == [((CUSTOMER, "promo_claimed"), {"code": "NOV20"})]


def test_a_forged_code_gets_nothing(shop, monkeypatch):
    bot = fakes.RecordingBot()

    tracked = _claim(bot, monkeypatch, "<b>x</b>")

    assert [c for c in bot.calls if isinstance(c, SendMessage)] == []
    assert tracked == []


# --- the promo code looked up in Shopify ---------------------------------------------

class FakeShop:
    """The shop's admin as the lookup port sees it."""

    def __init__(self, promo: Promo | None = None, fails: bool = False) -> None:
        self.promo, self.fails, self.asked = promo, fails, []

    async def find(self, code: str) -> Promo | None:
        self.asked.append(code)
        if self.fails:
            raise DiscountLookupFailed("timed out")
        return self.promo


def _promo_checked(bot, state, text, shop) -> None:
    asyncio.run(mod.process_broadcast_promo(
        fakes.message(bot, STAFF, text=text), _config(), state, None,
        promo_lookup=shop))


def _at_promo_question(bot) -> fakes.State:
    state = _at_picker(bot)
    _tap(bot, state, BroadcastAction(action="promo"))
    return state


def _staff_texts(bot) -> list[str]:
    return [c.text for c in bot.to(STAFF) if isinstance(c, SendMessage)]


def test_a_code_the_shop_has_is_confirmed_with_its_terms_and_used(shop):
    ends = datetime.now(timezone.utc) + timedelta(days=17)
    found = Promo("NOV20", status="ACTIVE", ends_at=ends, usage_limit=100, used=12,
                  percent=20.0)
    bot = fakes.RecordingBot()
    state = _at_promo_question(bot)

    _promo_checked(bot, state, "NOV20", FakeShop(found))

    day = ends.astimezone(SHOP_TZ).strftime("%d.%m.%Y")
    assert UK.MSG_BROADCAST_PROMO_FOUND.format(
        code="NOV20", details=f"−20%, до {day}, використано 12 з 100") in _staff_texts(bot)
    assert state.data["broadcast_post"]["promo"]["code"] == "NOV20"
    assert state.state == BroadcastStates.waiting_confirm.state


@pytest.mark.parametrize("found, warning", [
    (None, UK.MSG_BROADCAST_PROMO_MISSING.format(code="NOV2O")),
    (Promo("NOV2O", status="ACTIVE", usage_limit=5, used=5),
     UK.MSG_BROADCAST_PROMO_USED_UP.format(code="NOV2O", used=5, limit=5)),
    (Promo("NOV2O", status="DISABLED"), UK.MSG_BROADCAST_PROMO_INACTIVE.format(code="NOV2O")),
])
def test_a_doubtful_code_is_warned_about_and_not_used_yet(shop, found, warning):
    bot = fakes.RecordingBot()
    state = _at_promo_question(bot)

    _promo_checked(bot, state, "NOV2O", FakeShop(found))

    last = _last_to_staff(bot)
    assert last.text == warning
    assert _buttons(last.reply_markup) == [UK.BTN_BROADCAST_PROMO_USE,
                                           UK.BTN_BROADCAST_PROMO_RETRY]
    assert "promo" not in state.data["broadcast_post"]
    assert state.state == BroadcastStates.waiting_promo.state


def test_an_expired_code_says_when_it_ended(shop):
    ended = datetime.now(timezone.utc) - timedelta(days=3)
    bot = fakes.RecordingBot()
    state = _at_promo_question(bot)

    _promo_checked(bot, state, "OCT10", FakeShop(Promo("OCT10", status="EXPIRED",
                                                       ends_at=ended)))

    assert _last_to_staff(bot).text == UK.MSG_BROADCAST_PROMO_EXPIRED.format(
        code="OCT10", date=ended.astimezone(SHOP_TZ).strftime("%d.%m.%Y"))


def test_use_anyway_takes_the_code_the_shop_did_not_confirm(shop):
    """The check is advice. The code may be created a minute from now."""
    bot = fakes.RecordingBot()
    state = _at_promo_question(bot)
    _promo_checked(bot, state, "NEW-CODE", FakeShop(None))

    asyncio.run(mod.process_broadcast_promo_choice(
        fakes.callback(bot, STAFF, BroadcastAction(action="promo_use").pack()),
        BroadcastAction(action="promo_use"), _config(), state, None))

    assert state.data["broadcast_post"]["promo"]["code"] == "NEW-CODE"
    assert state.state == BroadcastStates.waiting_confirm.state
    assert any(isinstance(c, EditMessageReplyMarkup) for c in bot.calls)


def test_type_another_asks_again_and_keeps_nothing(shop):
    bot = fakes.RecordingBot()
    state = _at_promo_question(bot)
    _promo_checked(bot, state, "NOV2O", FakeShop(None))

    asyncio.run(mod.process_broadcast_promo_choice(
        fakes.callback(bot, STAFF, BroadcastAction(action="promo_retry").pack()),
        BroadcastAction(action="promo_retry"), _config(), state, None))

    assert _screen(bot).text == UK.MSG_BROADCAST_ASK_PROMO
    assert state.data.get("promo_pending") is None
    assert state.state == BroadcastStates.waiting_promo.state


def test_a_shop_that_does_not_answer_is_said_and_left_to_the_sender(shop):
    bot = fakes.RecordingBot()
    state = _at_promo_question(bot)

    _promo_checked(bot, state, "NOV20", FakeShop(fails=True))

    assert _last_to_staff(bot).text == UK.MSG_BROADCAST_PROMO_UNCHECKED.format(code="NOV20")
    assert state.state == BroadcastStates.waiting_promo.state


def test_without_a_shop_token_the_code_is_taken_as_typed(shop):
    bot = fakes.RecordingBot()
    state = _at_promo_question(bot)

    _promo_checked(bot, state, "NOV20", None)

    assert state.data["broadcast_post"]["promo"]["code"] == "NOV20"


def test_a_stale_button_on_the_picker_no_longer_cancels_the_post(shop):
    """«use anyway» or «back» tapped after its screen moved on used to reach
    the picker's handler as «not send», which meant «cancel»."""
    bot = fakes.RecordingBot()
    state = _at_picker(bot)

    for stale in ("promo_use", "back"):
        _tap(bot, state, BroadcastAction(action=stale))

    assert state.state == BroadcastStates.waiting_confirm.state
    assert UK.MSG_BROADCAST_CANCELLED not in [c.text for c in bot.calls
                                              if isinstance(c, EditMessageText)]


# --- the sender edits the post in Telegram ---------------------------------------------

def _edit(bot, state, **content) -> None:
    """The sender's message edited in place: same id, new content."""
    taken = state.data["broadcast_post"]["message"]["message_id"]
    asyncio.run(mod.process_broadcast_edit(
        fakes.message(bot, STAFF, message_id=taken, edit_date=1762960100, **content),
        _config(), state))


def test_an_edited_post_replaces_the_one_taken_and_keeps_everything_else(shop):
    bot = fakes.RecordingBot()
    state = _at_picker(bot)                                 # «Знижка 20%»
    _tap(bot, state, BroadcastFilter(name="purchases"))     # → без покупок: chat 10
    _tap(bot, state, BroadcastAction(action="buttons"))
    _type(bot, state, "Каталог - https://ks.example/catalog")
    _tap(bot, state, BroadcastAction(action="promo"))
    _promo(bot, state, "NOV20")
    before = len(bot.calls)

    _edit(bot, state, text="Знижка 25%")

    note, preview, picker = [c for c in bot.calls[before:]
                             if isinstance(c, SendMessage) and c.chat_id == STAFF]
    assert note.text == UK.MSG_BROADCAST_POST_UPDATED
    assert preview.text == "Знижка 25%"
    [promo_row], [link_row] = preview.reply_markup.inline_keyboard
    assert promo_row.callback_data == "promo:NOV20"
    assert link_row.url == "https://ks.example/catalog"
    # The filter chosen before the edit is still the one on the screen.
    assert picker.text == UK.MSG_BROADCAST_AUDIENCE.format(count=1, total=3)
    assert state.data["broadcast_text"] == "Знижка 25%"
    assert state.state == BroadcastStates.waiting_confirm.state


def test_what_goes_out_is_the_edited_version(shop):
    bot = fakes.RecordingBot()
    state = _at_picker(bot)
    _edit(bot, state, text="Знижка 25%")

    _send(bot, state)

    texts = {json.loads(row["payload"])["post"]["message"]["text"]
             for row in asyncio.run(claim(50))}
    assert texts == {"Знижка 25%"}


def test_an_edit_while_the_name_is_asked_asks_again(shop):
    """A name typed after an edit must answer for the post as it now is."""
    bot = fakes.RecordingBot()
    state = _at_picker(bot)
    _tap(bot, state, BroadcastAction(action="send"))
    assert state.state == BroadcastStates.waiting_name.state

    _edit(bot, state, text="Знижка 25%")

    assert state.state == BroadcastStates.waiting_confirm.state
    assert asyncio.run(claim(50)) == []


def test_an_edit_of_some_other_message_changes_nothing(shop):
    bot = fakes.RecordingBot()
    state = _at_picker(bot)
    before = len(bot.calls)

    asyncio.run(mod.process_broadcast_edit(
        fakes.message(bot, STAFF, text="інше повідомлення", edit_date=1762960100),
        _config(), state))

    assert bot.calls[before:] == []
    assert state.data["broadcast_post"]["message"]["text"] == "Знижка 20%"


def test_an_edited_caption_in_an_album_replaces_that_part_only(shop):
    bot = fakes.RecordingBot()
    state = _at_picker(bot)
    parts = [fakes.message(bot, STAFF, media_group_id="g", **fakes.photo(f"p{i}",
             caption="Стара підпис" if i == 0 else None)) for i in range(2)]
    state.data["broadcast_post"] = mod.post.album(parts)

    asyncio.run(mod.process_broadcast_edit(
        fakes.message(bot, STAFF, message_id=parts[0].message_id, media_group_id="g",
                      edit_date=1762960100, **fakes.photo("p0", caption="Нова підпис")),
        _config(), state))

    first, second = state.data["broadcast_post"]["album"]
    assert first["caption"] == "Нова підпис"
    assert second["message_id"] == parts[1].message_id


def test_buttons_that_cannot_follow_onto_an_album_are_said_to_be_dropped(shop):
    bot = fakes.RecordingBot()
    state = _at_picker(bot)
    _tap(bot, state, BroadcastAction(action="buttons"))
    _type(bot, state, "Каталог - https://ks.example/catalog")
    album = [fakes.message(bot, STAFF, media_group_id="g", **fakes.photo(f"p{i}"))
             for i in range(2)]

    asyncio.run(mod._offer(bot, STAFF, mod.post.album(album), state, UK, 2,
                           kept=dict(state.data)))

    assert UK.MSG_BROADCAST_BUTTONS_ALBUM in _staff_texts(bot)
    assert "buttons" not in state.data["broadcast_post"]


def test_a_fresh_broadcast_forgets_the_last_one(shop):
    bot = fakes.RecordingBot()
    state = _at_picker(bot)
    state.data["promo_pending"] = "OLD"

    asyncio.run(mod.cmd_broadcast(fakes.message(bot, STAFF, text="/broadcast"),
                                  _config(), state, None))

    assert state.data == {}
    assert state.state == BroadcastStates.waiting_message.state
