"""The key that has to be identical in three places, or the funnel loses a step.

Queued in the outbox header, carried in the button's callback_data, counted in
events. The tests are about the two ways that breaks: a value that cannot make
the round trip, and a value that does not fit where it has to go.
"""
from __future__ import annotations

from datetime import date

import pytest

from core.domain.campaign import MAX_LEN, CampaignKey, daily, parse


def test_a_key_renders_and_parses_back():
    key = CampaignKey("stock", "260819")
    assert str(key) == "stock.260819"
    assert parse(str(key)) == key


def test_a_daily_key_buckets_by_day():
    """Not per run: the restock sweep runs every fifteen minutes, and ninety-six
    campaigns a day is not a report anybody reads."""
    assert str(daily("stock", date(2026, 8, 19))) == "stock.260819"


def test_the_separator_is_not_the_one_aiogram_uses():
    """`:` splits callback_data into fields, so a key containing one arrives as
    two fields or not at all."""
    assert ":" not in str(daily("stock", date(2026, 8, 19)))
    with pytest.raises(ValueError):
        CampaignKey("sto:ck", "260819")


def test_a_key_fits_in_a_callback_with_room_to_spare():
    """64 bytes is the whole of callback_data, and the key is one field of
    several — next to a prefix, an action and sometimes a sku."""
    assert len(str(daily("delivery"[:8], date(2026, 8, 19)))) <= MAX_LEN
    assert MAX_LEN < 32


@pytest.mark.parametrize(
    "kind,ref",
    [
        ("", "260819"),          # no kind
        ("S", "260819"),         # single char, and upper case
        ("stock", ""),           # no ref
        ("stock", "2026-08-19"), # dashes are not allowed in a ref
        ("stock name", "1"),     # spaces would break the packing
        ("averylongkind", "1"),  # over eight characters
        ("stock", "123456789"),  # ref over eight characters
    ],
)
def test_a_malformed_key_is_refused_at_construction(kind, ref):
    with pytest.raises(ValueError):
        CampaignKey(kind, ref)


def test_parsing_something_that_is_not_a_key_raises():
    """Rather than returning a blank one. A malformed key means a message built
    by code that no longer exists, and counting it as an unnamed campaign
    corrupts every funnel it lands in."""
    for raw in ("", "stock", "stock:260819", ".260819"):
        with pytest.raises(ValueError):
            parse(raw)


def test_keys_are_comparable_and_hashable():
    """They are grouped by in reports and used as dictionary keys in the sender."""
    assert CampaignKey("stock", "260819") == CampaignKey("stock", "260819")
    assert len({CampaignKey("stock", "260819"), CampaignKey("stock", "260819")}) == 1
    assert CampaignKey("stock", "260819") != CampaignKey("stock", "260820")
