"""The first sender on the outbox, and the first tests this feature has ever had.

It had none while it lived in bot/stock.py, because reaching it meant building
an aiogram Bot and a message. Now the poll answers from a port and the message
lands in a table, so a restock is testable end to end with neither.
"""
from __future__ import annotations

import asyncio
import json
from datetime import date

import pytest

from core.domain.stock import restocked
from core.repos import base as repos_base
from core.repos.outbox import claim
from core.repos.schema import init_db
from core.repos.stock import (add_stock_subscription, get_stock_levels,
                              get_subscribed_skus, save_stock_levels)
from core.repos.users import save_user
from core.usecases.stock import CONFETTI_EFFECT_ID, check_once

CHAT = 555
OTHER = 556
TODAY = date(2026, 8, 19)


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(repos_base, "DB_PATH", str(tmp_path / "bot_data.db"))
    asyncio.run(init_db())


class FakeCatalogue:
    def __init__(self, levels: dict[str, int]) -> None:
        self._levels = levels

    async def get_stock(self) -> dict[str, int]:
        return dict(self._levels)


def _subscribe(chat_id: int, sku: str, name: str) -> None:
    asyncio.run(save_user(chat_id, f"+38067000{chat_id}"))
    asyncio.run(add_stock_subscription(chat_id, sku, name))


def _sweep(levels: dict[str, int]):
    return asyncio.run(check_once(FakeCatalogue(levels), today=TODAY))


def _queued() -> list[dict]:
    return asyncio.run(claim(50))


# --- the transition rule ----------------------------------------------------

def test_only_a_transition_counts():
    assert restocked({"A": 0}, {"A": 3}) == ["A"]
    assert restocked({"A": 3}, {"A": 3}) == [], "in stock and staying in stock"
    assert restocked({"A": 0}, {"A": 0}) == []


def test_a_sku_nobody_has_seen_before_is_not_a_restock():
    """On the first poll that rule is the difference between one quiet baseline
    and the whole catalogue going out at once."""
    assert restocked({}, {"A": 5}) == []


# --- the sweep --------------------------------------------------------------

def test_the_first_sweep_only_records_a_baseline(db):
    _subscribe(CHAT, "A", "Cream")
    result = _sweep({"A": 5})

    assert result.baseline is True
    assert _queued() == []
    assert asyncio.run(get_stock_levels()) == {"A": 5}


def test_a_restock_queues_one_message_for_the_subscriber(db):
    _subscribe(CHAT, "A", "Neogen Cream 50ml")
    asyncio.run(save_stock_levels({"A": 0}))

    result = _sweep({"A": 4})

    assert (result.skus_back, result.chats_queued) == (1, 1)
    [row] = _queued()
    assert row["chat_id"] == CHAT
    assert row["type"] == "stock"
    assert row["campaign_key"] == "stock.260819"
    payload = json.loads(row["payload"])
    assert "Neogen Cream 50ml" in payload["text"]
    assert payload["effect_id"] == CONFETTI_EFFECT_ID


def test_several_products_for_one_person_are_one_message(db):
    """Several favourites can return in the same sweep, and that should not read
    as a burst of notifications."""
    _subscribe(CHAT, "A", "Cream")
    _subscribe(CHAT, "B", "Toner")
    asyncio.run(save_stock_levels({"A": 0, "B": 0}))

    _sweep({"A": 1, "B": 2})

    rows = _queued()
    assert len(rows) == 1
    text = json.loads(rows[0]["payload"])["text"]
    assert "Cream" in text and "Toner" in text


def test_two_people_waiting_for_the_same_product_each_get_one(db):
    _subscribe(CHAT, "A", "Cream")
    _subscribe(OTHER, "A", "Cream")
    asyncio.run(save_stock_levels({"A": 0}))

    _sweep({"A": 1})

    assert sorted(row["chat_id"] for row in _queued()) == [CHAT, OTHER]


def test_the_subscription_is_cleared_once_the_message_is_queued(db):
    """The promise is "tell me when it is back", once. From here the queue is
    what guarantees the telling — which the direct send never did."""
    _subscribe(CHAT, "A", "Cream")
    asyncio.run(save_stock_levels({"A": 0}))

    _sweep({"A": 1})

    assert asyncio.run(get_subscribed_skus(CHAT)) == set()


def test_a_crash_between_queueing_and_clearing_does_not_double_the_message(db):
    """The dedup key earns its place here: after a restart the subscriptions are
    still there, the sweep runs again, and the customer must not be told twice."""
    _subscribe(CHAT, "A", "Cream")
    asyncio.run(save_stock_levels({"A": 0}))
    asyncio.run(check_once(FakeCatalogue({"A": 1}), today=TODAY))

    # As if the clearing never happened.
    _subscribe(CHAT, "A", "Cream")
    asyncio.run(save_stock_levels({"A": 0}))
    result = asyncio.run(check_once(FakeCatalogue({"A": 1}), today=TODAY))

    assert result.chats_queued == 0, "the second copy was refused by the dedup key"
    assert len(_queued()) == 1


def test_a_failed_read_changes_nothing(db):
    """{} means the read failed. Recording it would announce a restock of the
    entire catalogue on the next poll."""
    asyncio.run(save_stock_levels({"A": 0}))
    _subscribe(CHAT, "A", "Cream")

    result = _sweep({})

    assert result == type(result)()
    assert asyncio.run(get_stock_levels()) == {"A": 0}
    assert _queued() == []


def test_a_return_nobody_is_waiting_for_is_silent(db):
    asyncio.run(save_stock_levels({"A": 0}))
    result = _sweep({"A": 5})

    assert (result.skus_back, result.chats_queued) == (1, 0)
    assert _queued() == []
