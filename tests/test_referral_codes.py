"""Every reward gets a code of its own, or the promise falls back a step.

Until now one code paid every referral: whatever the shop had written in its
admin, handed to everybody who earned one. That is a code loose in the world —
forwarded, posted, spent by strangers — and the shop never agreed to any of it.

With a Shopify token that may create discounts, each reward gets a single-use
code created as it is paid. What matters most here is what happens when that
fails, because it will: the promise must fall back, never break.
"""
from __future__ import annotations

import asyncio

import pytest

from core.domain.referral import Earned
from core.i18n import Texts
from core.usecases.referrals import check_once

PREFIX = "ref_"
FRIEND, REFERRER = 501, 502
T = Texts("uk")


class _Ledger:
    def __init__(self, pairs):
        self._pairs = pairs
        self.codes: list[tuple[int, str]] = []

    async def earned(self, prefix, limit):
        return self._pairs

    async def record_reward(self, friend_chat_id, referrer_chat_id):
        return True

    async def record_code(self, friend_chat_id, code):
        self.codes.append((friend_chat_id, code))


class _Languages:
    async def chosen_by(self, chat_id):
        return "uk"


class _Queue:
    def __init__(self):
        self.sent: list[dict] = []

    async def queue(self, chat_id, kind, campaign, payload, **kwargs):
        self.sent.append({"chat_id": chat_id, **payload})
        return len(self.sent)


class _Shopify:
    """Hands out codes in order, or None where the list says so."""

    def __init__(self, *codes):
        self._codes = list(codes)
        self.asked: list[tuple[int, str]] = []

    async def issue_percentage(self, percent, *, title):
        self.asked.append((percent, title))
        return self._codes.pop(0) if self._codes else None


def _run(discounts=None, *, percent=10, code="", pairs=None, ledger=None):
    ledger = ledger or _Ledger(pairs or [Earned(FRIEND, REFERRER)])
    queue = _Queue()
    asyncio.run(check_once(
        PREFIX, ledger, _Languages(), queue,
        code=code, reward="Знижка 10%", discount_link="https://shop/static",
        discounts=discounts, percent=percent,
        link_for=lambda issued: f"https://shop/discount/{issued}",
    ))
    return ledger, queue


def test_each_reward_gets_its_own_code():
    shopify = _Shopify("KS-AAAA1111", "KS-BBBB2222")
    ledger = _Ledger([Earned(601, 602), Earned(701, 702)])
    ledger, queue = _run(shopify, ledger=ledger)

    codes = [c for _friend, c in ledger.codes]
    assert codes == ["KS-AAAA1111", "KS-BBBB2222"], "one code, one reward"
    assert len(set(codes)) == 2


def test_the_customer_is_told_the_code_that_was_made_for_her():
    shopify = _Shopify("KS-AAAA1111")
    _ledger, queue = _run(shopify)
    assert "KS-AAAA1111" in queue.sent[0]["text"]
    button = queue.sent[0]["keyboard"]["inline_keyboard"][0][0]
    assert button["url"] == "https://shop/discount/KS-AAAA1111"


def test_the_title_says_who_it_was_for():
    """A row in the shop's admin that nobody can explain is a row nobody dares
    delete."""
    shopify = _Shopify("KS-AAAA1111")
    _run(shopify)
    _percent, title = shopify.asked[0]
    assert str(REFERRER) in title


def test_a_failed_code_falls_back_to_the_configured_one():
    """Shopify is down, the shop has a code written in its admin: she gets that
    one rather than nothing."""
    _ledger, queue = _run(_Shopify(None), code="STATIC10")
    assert "STATIC10" in queue.sent[0]["text"]
    button = queue.sent[0]["keyboard"]["inline_keyboard"][0][0]
    assert button["url"] == "https://shop/static"


def test_with_no_code_anywhere_a_person_writes_one():
    """The oldest path, and still the last one: the message says a manager will
    send a code, and the note in the support chat asks them to."""
    _ledger, queue = _run(_Shopify(None), code="")
    assert T.MSG_REFERRAL_BY_HAND.strip() in queue.sent[0]["text"]
    assert "keyboard" not in queue.sent[0]


def test_without_a_percentage_the_shop_is_never_asked():
    """A percentage of nothing is a configuration mistake, and asking Shopify
    to record one would be the bot inventing a reward."""
    shopify = _Shopify("KS-AAAA1111")
    _ledger, queue = _run(shopify, percent=0, code="STATIC10")
    assert not shopify.asked
    assert "STATIC10" in queue.sent[0]["text"]


def test_no_port_is_the_behaviour_that_shipped_before_it():
    _ledger, queue = _run(None, code="STATIC10")
    assert "STATIC10" in queue.sent[0]["text"]
