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
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
from aiogram.exceptions import TelegramForbiddenError
from aiogram.methods import EditMessageText, SendMessage

import broadcast_fakes as fakes
from bot.callbacks import BroadcastAction, BroadcastFilter
from bot.handlers import broadcast as mod
from bot.states import BroadcastStates
from core.domain.audience import Audience, Profile, Purchase, matches
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
               else mod.process_broadcast_confirm)
    asyncio.run(handler(fakes.callback(bot, STAFF, data.pack()), data,
                        _config(), state, None))


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
    _tap(bot, state, BroadcastAction(action="send"))

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
    # A fresh picker under the copies, with the filters as they were.
    picker = new[-1]
    assert isinstance(picker, SendMessage) and picker.chat_id == STAFF
    assert picker.text == UK.MSG_BROADCAST_AUDIENCE.format(count=1, total=3)
    assert state.state == BroadcastStates.waiting_confirm.state


def test_the_real_send_still_works_after_a_test(shop):
    bot = fakes.RecordingBot()
    state = _at_picker(bot)
    _tap(bot, state, BroadcastAction(action="test"))
    _tap(bot, state, BroadcastAction(action="send"))

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
