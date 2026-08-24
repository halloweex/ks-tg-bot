"""Who gets greeted, who gets asked, and who gets asked only once.

The sweep does two things a day apart in cost: it reads a column, and it asks
Telegram. Most of these tests are about the second one staying cheap — an empty
answer is an answer, a failed ask is not, and nobody is asked twice in an
afternoon.

No bot anywhere: the profile source is a port, so a birthday can be tested with
a dict.
"""
from __future__ import annotations

import asyncio
import json
from datetime import date

import pytest

from core.repos import base as repos_base
from core.repos.outbox import claim
from core.repos.schema import init_db
from core.repos.users import (chats_without_birthday, opt_out_user, save_birthday,
                              save_user)
from core.usecases.birthdays import ASK_PER_RUN, KIND, check_once

TODAY = date(2026, 8, 25)


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(repos_base, "DB_PATH", str(tmp_path / "bot_data.db"))
    asyncio.run(init_db())


class _Profiles:
    """The port, answering from a dict and counting the asks."""

    def __init__(self, answers: dict[int, str | None]) -> None:
        self.answers = answers
        self.asked: list[int] = []

    async def get_birthday(self, chat_id: int) -> str | None:
        self.asked.append(chat_id)
        return self.answers.get(chat_id, "")


def _customer(chat_id: int, birthdate: str | None = None) -> None:
    asyncio.run(save_user(chat_id, f"+3806700000{chat_id:02d}"))
    if birthdate is not None:
        asyncio.run(save_birthday(chat_id, birthdate))


def _queued() -> list[dict]:
    return [{**row, "payload": json.loads(row["payload"])}
            for row in asyncio.run(claim(50)) if row["type"] == KIND]


def _run(profiles=None, *, today: date = TODAY):
    return asyncio.run(check_once(profiles or _Profiles({}), today=today))


# --- the greeting -----------------------------------------------------------

def test_a_customer_born_today_is_greeted(db):
    _customer(1, "08-25")
    assert _run().greeted == 1
    [message] = _queued()
    assert message["chat_id"] == 1
    assert "днем народження" in message["payload"]["text"]


def test_the_greeting_carries_their_own_list(db):
    """The one thing it can usefully offer. No discount: there is no discount
    policy yet, and a bot inventing one commits the shop to it."""
    _customer(1, "08-25")
    _run()
    message = _queued()[0]
    button = message["payload"]["keyboard"]["inline_keyboard"][0][0]
    assert button["switch_inline_query_current_chat"] == ""
    assert "знижк" not in message["payload"]["text"], "no discount is promised"


def test_nobody_else_is_greeted(db):
    _customer(1, "08-24")
    _customer(2, "12-25")
    _customer(3, "")
    assert _run().greeted == 0
    assert _queued() == []


def test_a_birthday_is_wished_once_a_day_however_often_the_sweep_runs(db):
    """It runs hourly — a deploy would otherwise cost somebody twelve of
    these."""
    _customer(1, "08-25")
    assert _run().greeted == 1
    assert _run().greeted == 0
    assert len(_queued()) == 1


def test_somebody_who_opted_out_is_not_greeted(db):
    """A greeting is still something the bot decided to send."""
    _customer(1, "08-25")
    asyncio.run(opt_out_user(1))
    assert _run().greeted == 0


def test_the_greeting_waits_for_the_morning(db):
    """Quiet hours apply: this is the bot's own idea, unlike a manager's
    answer, and 03:00 is not when anybody wants to be congratulated."""
    _customer(1, "08-25")
    _run()
    assert _queued()[0]["respect_quiet"] == 1


# --- asking Telegram --------------------------------------------------------

def test_a_customer_nobody_has_asked_about_is_asked(db):
    _customer(1)
    profiles = _Profiles({1: "03-14"})
    assert _run(profiles).learned == 1
    assert profiles.asked == [1]
    assert asyncio.run(chats_without_birthday(10)) == []


def test_having_no_birthday_is_an_answer_and_is_remembered(db):
    """Most people have not set one, or hide it. Storing the empty answer is
    what stops the sweep asking them again every hour."""
    _customer(1)
    profiles = _Profiles({1: ""})
    assert _run(profiles).asked == 1
    assert asyncio.run(chats_without_birthday(10)) == []


def test_a_failed_ask_is_not_recorded_as_an_answer(db):
    """None means the ask itself failed — a blocked bot, a chat Telegram will
    not resolve. That person is asked again next time."""
    _customer(1)
    profiles = _Profiles({1: None})
    assert _run(profiles).asked == 0
    assert asyncio.run(chats_without_birthday(10)) == [1]


def test_only_a_slice_is_asked_about_per_run(db):
    """One API call per customer per quarter is the whole budget of this
    feature; asking about everybody at once is how that stops being true."""
    for chat_id in range(1, ASK_PER_RUN + 10):
        _customer(chat_id)
    profiles = _Profiles({})
    _run(profiles)
    assert len(profiles.asked) == ASK_PER_RUN


def test_the_ones_asked_about_are_the_ones_nobody_asked_yet(db):
    _customer(1, "01-01")
    _customer(2)
    profiles = _Profiles({})
    _run(profiles)
    assert profiles.asked == [2]
