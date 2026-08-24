"""What the offers cache promises the screens that read it."""
from __future__ import annotations

import asyncio

import pytest

from core.domain.offer import Offer
from core.repos import base as repos_base
from core.repos.catalogue import count_offers, get_offers, save_offers
from core.repos.schema import init_db


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(repos_base, "DB_PATH", str(tmp_path / "bot_data.db"))
    asyncio.run(init_db())


def _offer(sku, *, variant=1, available=True, price="100.00", title="T") -> Offer:
    return Offer(sku=sku, variant_id=variant, handle="h", title=title,
                 price=price, available=available)


def test_offers_survive_a_round_trip(db):
    asyncio.run(save_offers({"1": _offer("1", variant=9, price="680.00")}))
    got = asyncio.run(get_offers(["1"]))
    assert got["1"].variant_id == 9
    assert got["1"].price == "680.00"
    assert got["1"].available is True


def test_a_later_sweep_overwrites_what_the_earlier_one_knew(db):
    """Price and availability both move, and the screen must show today's."""
    asyncio.run(save_offers({"1": _offer("1", price="680.00", available=True)}))
    asyncio.run(save_offers({"1": _offer("1", price="720.00", available=False)}))
    got = asyncio.run(get_offers(["1"]))
    assert (got["1"].price, got["1"].available) == ("720.00", False)


def test_an_empty_sweep_writes_nothing(db):
    """The adapter returns {} for a failed read. Taking that literally would
    empty the cache and take the buy button off the whole catalogue."""
    asyncio.run(save_offers({"1": _offer("1")}))
    asyncio.run(save_offers({}))
    assert asyncio.run(count_offers()) == 1


def test_only_the_skus_asked_for_come_back(db):
    asyncio.run(save_offers({s: _offer(s) for s in ("1", "2", "3")}))
    assert set(asyncio.run(get_offers(["1", "3", "missing"]))) == {"1", "3"}


def test_asking_for_nothing_touches_no_database(db):
    """A customer whose order lines carry no sku at all — the older rows in the
    cache have none — must not produce `WHERE sku IN ()`."""
    assert asyncio.run(get_offers([])) == {}
    assert asyncio.run(get_offers(["", "  "])) == {}
