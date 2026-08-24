"""The catalogue sweep: what it writes, and what it refuses to write."""
from __future__ import annotations

import asyncio

import pytest

from core.domain.offer import Offer
from core.repos import base as repos_base
from core.repos.catalogue import count_offers, get_offers
from core.repos.schema import init_db
from core.usecases.sync_catalogue import refresh_once


class FakeStorefront:
    """A storefront that answers from a dict. Implements the port."""

    def __init__(self, offers: dict[str, Offer]) -> None:
        self._offers = offers

    async def get_offers(self) -> dict[str, Offer]:
        return self._offers


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(repos_base, "DB_PATH", str(tmp_path / "bot_data.db"))
    asyncio.run(init_db())


def _offer(sku, available=True) -> Offer:
    return Offer(sku=sku, variant_id=int(sku), handle="h", title="T",
                 price="100.00", available=available)


def test_a_sweep_writes_what_the_shop_says(db):
    seen = asyncio.run(refresh_once(FakeStorefront({"1": _offer("1"), "2": _offer("2")})))
    assert seen == 2
    assert set(asyncio.run(get_offers(["1", "2"]))) == {"1", "2"}


def test_a_failed_read_leaves_yesterday_standing(db):
    """The adapter says {} when it could not read the shop. Writing that through
    would take the buy button off every product until the next round — an hour
    of a working shop looking sold out because of one 500."""
    asyncio.run(refresh_once(FakeStorefront({"1": _offer("1")})))
    assert asyncio.run(refresh_once(FakeStorefront({}))) == 0
    assert asyncio.run(count_offers()) == 1
    assert asyncio.run(get_offers(["1"]))["1"].available is True
