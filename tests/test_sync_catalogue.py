"""The catalogue sweep: what it writes, and what it refuses to write."""
from __future__ import annotations

import asyncio

import pytest

from core.domain.offer import Offer
from core.repos import base as repos_base
from core.repos.catalogue import SqliteOfferCache, count_offers, get_offers
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
    seen = asyncio.run(refresh_once(
        FakeStorefront({"1": _offer("1"), "2": _offer("2")}), SqliteOfferCache()))
    assert seen == 2
    assert set(asyncio.run(get_offers(["1", "2"]))) == {"1", "2"}


def test_the_row_is_written_under_the_offer_s_own_sku(db):
    """The cache reads the values and not the keys, which the port now says
    rather than implies. Worth a line of its own because the two normally agree
    — one parse builds both — so the disagreement only ever shows up in a
    caller who re-keyed the mapping and expected that to rename something."""
    asyncio.run(SqliteOfferCache().replace({"not-a-sku": _offer("1")}))
    assert list(asyncio.run(get_offers(["1", "not-a-sku"]))) == ["1"]


# --- what the shop has stopped listing stops being sold ----------------------
#
# The cache was upsert-only and never pruned, deliberately: the port said a
# short read was indistinguishable from a shrunken catalogue. It no longer is —
# the adapter answers {} at the page cap as well as on a transport error — so
# absence from a sweep now means the storefront has stopped listing the product,
# and the row goes with it. Until it did, an unpublished product kept
# `available = 1` and its last price forever, and the favourites screen went on
# offering «Купити» for something the shop does not sell.


def test_a_product_the_shop_stopped_listing_is_dropped(db):
    asyncio.run(refresh_once(
        FakeStorefront({"1": _offer("1"), "2": _offer("2")}), SqliteOfferCache()))
    asyncio.run(refresh_once(
        FakeStorefront({"1": _offer("1")}), SqliteOfferCache()))

    assert list(asyncio.run(get_offers(["1", "2"]))) == ["1"], (
        "a delisted product kept its buy button and its last price")
    assert asyncio.run(count_offers()) == 1


def test_sold_out_is_not_delisted(db):
    """The distinction the pruning rests on. An unpublished product leaves the
    feed; one that is merely sold out stays in it with available: false. Pruning
    on absence is only right because those two are different."""
    asyncio.run(refresh_once(
        FakeStorefront({"1": _offer("1")}), SqliteOfferCache()))
    asyncio.run(refresh_once(
        FakeStorefront({"1": _offer("1", available=False)}), SqliteOfferCache()))

    kept = asyncio.run(get_offers(["1"]))
    assert "1" in kept, "sold out is not gone: the row must stay"
    assert kept["1"].available is False


def test_the_sweep_says_how_many_it_removed(db):
    asyncio.run(refresh_once(
        FakeStorefront({s: _offer(s) for s in ("1", "2", "3")}),
        SqliteOfferCache()))
    removed = asyncio.run(SqliteOfferCache().replace({"1": _offer("1")}))
    assert removed == 2, "a silent prune is the thing this change must not be"


def test_a_sweep_that_dies_halfway_changes_nothing(db):
    """Write and delete are one statement about what the shop sells, and a
    sweep that failed between them would leave a catalogue that never existed.

    Worth its own test because the transaction is implicit: `connect()` runs in
    sqlite3's legacy mode, where BEGIN is issued before DML but not before DDL —
    and `replace_offers` opens with DDL, the CREATE TEMP TABLE. The rollback is
    therefore a property of where the first DML statement sits, which is not
    something a reader can see from the function."""
    import sqlite3

    from core.repos.catalogue import replace_offers

    asyncio.run(refresh_once(
        FakeStorefront({s: _offer(s) for s in ("1", "2", "3")}),
        SqliteOfferCache()))

    # variant_id is INTEGER NOT NULL, so this raises inside the upsert — after
    # the surviving skus are in place and before anything is deleted.
    broken = Offer(sku="9", variant_id=None, handle="h", title="T",
                   price="1.00", available=True)
    with pytest.raises(sqlite3.IntegrityError):
        asyncio.run(replace_offers({"9": broken}))

    assert asyncio.run(count_offers()) == 3, (
        "a failed sweep took rows with it")
    assert set(asyncio.run(get_offers(["1", "2", "3"]))) == {"1", "2", "3"}


# --- one sweep may not unpublish the shop ------------------------------------
#
# The adapter retries an empty page, which separates a transient answer from a
# real end of feed and cannot separate a persistent one — an empty page IS how
# this feed says "that was the last one". So the scenario keeps a signal that
# does not come from the feed at all. Measured before it existed: page 2 of 3
# answering 200 with an empty list deleted 350 of 600 rows.


def test_a_sweep_that_lost_half_the_shop_writes_but_does_not_prune(db):
    asyncio.run(refresh_once(
        FakeStorefront({s: _offer(s) for s in ("1", "2", "3", "4")}),
        SqliteOfferCache()))

    asyncio.run(refresh_once(
        FakeStorefront({"1": _offer("1", available=False)}), SqliteOfferCache()))

    assert asyncio.run(count_offers()) == 4, (
        "a short feed took three quarters of the catalogue with it")
    assert asyncio.run(get_offers(["1"]))["1"].available is False, (
        "the offers it did read are still worth writing")


def test_a_plausible_shrink_still_prunes(db):
    """The guard is against a catalogue halving in an hour, not against the
    ordinary business of a shop delisting things."""
    asyncio.run(refresh_once(
        FakeStorefront({s: _offer(s) for s in ("1", "2", "3", "4")}),
        SqliteOfferCache()))
    asyncio.run(refresh_once(
        FakeStorefront({s: _offer(s) for s in ("1", "2", "3")}),
        SqliteOfferCache()))

    assert asyncio.run(count_offers()) == 3
    assert list(asyncio.run(get_offers(["4"]))) == []


def test_the_first_sweep_into_an_empty_table_prunes_nothing_and_is_not_refused(db):
    """Nothing cached means nothing to compare against, and the guard must not
    read that as a collapse."""
    assert asyncio.run(refresh_once(
        FakeStorefront({"1": _offer("1")}), SqliteOfferCache())) == 1
    assert asyncio.run(count_offers()) == 1


def test_a_failed_read_leaves_yesterday_standing(db):
    """The adapter says {} when it could not read the shop. Writing that through
    would take the buy button off every product until the next round — an hour
    of a working shop looking sold out because of one 500.

    The stakes went up when pruning arrived: under the old rule {} was merely
    useless, and under this one it would empty the shop."""
    asyncio.run(refresh_once(FakeStorefront({"1": _offer("1")}), SqliteOfferCache()))
    assert asyncio.run(refresh_once(FakeStorefront({}), SqliteOfferCache())) == 0
    assert asyncio.run(count_offers()) == 1
    assert asyncio.run(get_offers(["1"]))["1"].available is True


def test_the_cache_refuses_an_empty_mapping_on_its_own(db):
    """Not only the scenario. The port says the implementation must refuse too,
    because one guard is one more than can be deleted by a refactor that looks
    correct — and this is now the line between a failed read and an empty
    shop."""
    asyncio.run(refresh_once(
        FakeStorefront({s: _offer(s) for s in ("1", "2")}), SqliteOfferCache()))
    assert asyncio.run(SqliteOfferCache().replace({})) == 0
    assert asyncio.run(count_offers()) == 2
