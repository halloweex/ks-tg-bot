"""The messages the bot starts itself also know who they are addressed to.

Two of them carry a gendered sentence: the referral reward («Дякуємо, що порадила
нас») and the loyalty programme's version of the same news. They are the awkward
half of this feature — there is no update to resolve anything from, so the form
is read the same way the language is, from the column beside it.

The other two outbound messages, the restock notice and the birthday greeting,
are written without marking gender and are left alone. That is checked here too,
because "this one needs no form" is exactly the kind of claim that quietly stops
being true when somebody rewrites a string.
"""
from __future__ import annotations

import asyncio

from core.domain.gender import Gender
from core.domain.referral import Earned
from core.i18n import UK_MASCULINE, Texts
from core.usecases.loyalty import announce
from core.usecases.referrals import check_once

PREFIX = "ref_"
FRIEND, REFERRER = 501, 502


class _Ledger:
    def __init__(self, pairs):
        self._pairs = pairs

    async def earned(self, prefix, limit):
        return self._pairs

    async def record_reward(self, friend_chat_id, referrer_chat_id):
        return True

    async def record_code(self, friend_chat_id, code):
        pass


class _Languages:
    async def chosen_by(self, chat_id):
        return "uk"


class _Forms:
    def __init__(self, form: str | None):
        self._form = form
        self.asked: list[int] = []

    async def form_for(self, chat_id):
        self.asked.append(chat_id)
        return self._form

    async def remember(self, chat_id, form):
        raise AssertionError("an outbound message must not write a form")


class _Queue:
    def __init__(self):
        self.sent: list[dict] = []

    async def queue(self, chat_id, kind, campaign, payload, **kwargs):
        self.sent.append({"chat_id": chat_id, **payload})
        return len(self.sent)


def _referral(form: str | None) -> tuple[str, _Forms]:
    forms = _Forms(form)
    queue = _Queue()
    asyncio.run(check_once(PREFIX, _Ledger([Earned(FRIEND, REFERRER)]),
                           _Languages(), queue, forms=forms,
                           reward="Знижка 10%"))
    return queue.sent[0]["text"], forms


def test_a_man_is_thanked_as_one():
    text, forms = _referral(Gender.M.value)
    assert text.startswith(UK_MASCULINE["MSG_REFERRAL_EARNED"])
    assert "порадила" not in text
    assert forms.asked == [REFERRER], "the referrer's form, not the friend's"


def test_nothing_stored_keeps_the_wording_that_shipped():
    text, _ = _referral(None)
    assert text.startswith(Texts("uk").MSG_REFERRAL_EARNED)


def test_somebody_we_could_not_place_is_thanked_without_a_gender():
    text, _ = _referral(Gender.UNKNOWN.value)
    assert "порадила" not in text and "порадив" not in text


def test_a_scenario_given_no_form_port_at_all_still_runs():
    """The default, and what every existing caller and test passes: no port, no
    form, the feminine copy. A required argument here would have been a
    signature change in four places for a sentence in two."""
    queue = _Queue()
    asyncio.run(check_once(PREFIX, _Ledger([Earned(FRIEND, REFERRER)]),
                           _Languages(), queue, reward="Знижка 10%"))
    assert Texts("uk").MSG_REFERRAL_EARNED in queue.sent[0]["text"]


# --- the loyalty programme's own version of the same news --------------------
class _Chats:
    async def chat_for(self, email):
        return REFERRER


def _loyalty(form: str | None) -> str:
    from core.domain.loyalty import Kind, LoyaltyEvent

    queue = _Queue()
    event = LoyaltyEvent(kind=Kind.REFERRAL, email="somebody@example.com")
    assert asyncio.run(announce(event, _Chats(), _Languages(), queue,
                                forms=_Forms(form)))
    return queue.sent[0]["text"]


def test_the_loyalty_referral_note_follows_the_same_rule():
    assert _loyalty(Gender.M.value) == UK_MASCULINE["MSG_LOYALTY_REFERRAL"]
    assert _loyalty(None) == Texts("uk").MSG_LOYALTY_REFERRAL
    assert "порадила" not in _loyalty(Gender.UNKNOWN.value)


def test_the_two_outbound_messages_that_need_no_form_still_need_none():
    """A guard on a claim, not on behaviour: the restock notice and the birthday
    greeting are rendered with no form anywhere, and they may stay that way only
    for as long as their strings mark no gender."""
    from core.i18n import UK_NEUTRAL

    for key in ("MSG_BACK_IN_STOCK_HEADER", "MSG_SUBSCRIBED", "MSG_BIRTHDAY"):
        assert key not in UK_MASCULINE and key not in UK_NEUTRAL, (
            f"{key} has forms now — the sweep that renders it must pass one")
