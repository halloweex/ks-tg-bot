"""Two fixes the port series deliberately postponed, and what they cost.

Both were found during the move and written into docs/move-status.md rather than
fixed there, because a move that also fixes things is a move nobody can review.
Neither had ever fired in production, and both are the kind that would not look
like a bug when they did.
"""
from __future__ import annotations

import asyncio

import pytest

from core.domain.birthday import is_month_day, month_day
from core.repos import base as repos_base
from core.repos.referrals import earned_referrals
from core.repos.schema import init_db
from core.repos.users import chats_with_birthday_on, save_birthday, save_user

PREFIX = "ref_"
REFERRER = 100


async def _ordered(chat_id: int) -> None:
    """One delivered order, which is what makes a referral come good."""
    from core.repos.orders import upsert_orders

    await upsert_orders(chat_id, [{
        "chat_id": chat_id, "source": "keycrm", "source_order_id": str(chat_id),
        "external_id": str(chat_id), "order_name": "", "status_name": "delivered",
        "status_group_id": 1, "grand_total": 100.0, "currency": "грн",
        "ordered_at": "2026-08-01T10:00:00", "products_json": "[]",
        "buyer_name": "", "payment_status": "", "tracking_code": "",
        "shipping_status": "", "delivery_city": "", "receive_point": "",
        "recipient_name": "",
    }])


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(repos_base, "DB_PATH", str(tmp_path / "bot_data.db"))
    asyncio.run(init_db())


# --- the underscore that was a wildcard -------------------------------------

def test_a_source_that_only_looks_like_the_prefix_is_not_a_referral(db):
    """`ref_` under LIKE is a four-character pattern, not a four-character
    string: `_` matches any single character, so `refX100` and `refs100` were
    both read as referrals brought by chat 100.

    Nobody had such a link, which is exactly why it never showed. It would have
    started paying rewards for arrivals through some future prefix that happened
    to share three characters — and paying the wrong person is the failure this
    table exists to prevent.
    """
    asyncio.run(save_user(REFERRER, "+380670000100"))
    asyncio.run(save_user(201, "+380670000201", source=f"refX{REFERRER}"))
    asyncio.run(save_user(202, "+380670000202", source=f"{PREFIX}{REFERRER}"))
    for friend in (201, 202):
        asyncio.run(_ordered(friend))

    # Both arrived, both ordered; only one arrived through the real link.
    found = asyncio.run(earned_referrals(PREFIX, 50))
    assert [friend for friend, _ref in found] == [202]


# --- the month-day that was an empty string ---------------------------------

def test_a_blank_month_day_is_refused_rather_than_matching_everybody(db):
    """"" is a stored value with a meaning: asked, and Telegram showed no date.
    Most customers have it. So a search for "" matched nearly the whole base,
    and the birthday sweep would have wished all of them a happy birthday.

    The caller always passes strftime("%m-%d"), which is why this never fired.
    It is refused rather than returning nothing, because a query that quietly
    answers nothing for a bad argument is the same trap one layer down.
    """
    asyncio.run(save_user(301, "+380670000301"))
    asyncio.run(save_birthday(301, ""))          # asked, no date — the usual case
    asyncio.run(save_user(302, "+380670000302"))
    asyncio.run(save_birthday(302, "08-26"))

    with pytest.raises(ValueError):
        asyncio.run(chats_with_birthday_on(""))

    assert asyncio.run(chats_with_birthday_on("08-26")) == [302]


def test_what_counts_as_a_month_day():
    from datetime import date

    assert is_month_day("08-26") is True
    assert is_month_day("") is False, "the whole point"
    assert is_month_day("8-26") is False
    assert is_month_day("2026-08-26") is False
    assert is_month_day("13-01") is False
    assert is_month_day("02-30") is True, "a day nobody has, not a malformed one"
    assert month_day(date(2026, 8, 26)) == "08-26"
