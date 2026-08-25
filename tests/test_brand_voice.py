"""Two rules from the brand strategy that a machine can actually check.

The rest of Tone of voice (pp. 197–204) is judgement — dialogue over dispatch,
the person at the centre, no answering "шаблоном". These two are not: the deck
forbids the formal «ви» outright, and the long dash was asked to go. Both are
the kind of thing that creeps back one string at a time.

Operator and admin strings are exempt on purpose. They are read by managers and
by us, in a different register and a different language — see
core.i18n.operator_texts and admin_texts, and docs/components.md on who each
table is written for.
"""
from __future__ import annotations

import re

import pytest

from core.i18n import SUPPORTED, Texts

# Read from the support chat or the admin surfaces, never by a customer.
NOT_CUSTOMER_FACING = {
    "MSG_SUPPORT_ADMIN_NOTE", "MSG_SUPPORT_REPLY_INSTRUCTION",
    "MSG_SUPPORT_NO_REPLY_TARGET", "MSG_DISCOUNT_ADMIN", "MSG_FAVOURITE_LINE",
    "MSG_REFERRAL_ADMIN_SUMMARY", "MSG_CUSTOMER_UNKNOWN",
    "MSG_BROADCAST_PROMPT", "MSG_BROADCAST_CONFIRM", "MSG_BROADCAST_STARTED",
    "MSG_BROADCAST_COMPLETE", "MSG_BROADCAST_CANCELLED",
    "MSG_BROADCAST_NO_RECIPIENTS",
}


def _customer_strings(lang: str) -> dict[str, str]:
    t = Texts(lang)
    import core.texts as table

    return {
        key: getattr(t, key)
        for key in dir(table)
        if key.isupper()
        and key not in NOT_CUSTOMER_FACING
        and isinstance(getattr(t, key, None), str)
    }


@pytest.mark.parametrize("lang", sorted(SUPPORTED))
def test_no_long_dash_reaches_a_customer(lang):
    """The en dash in a range («Показано 1–5 з 12») is typography and stays;
    the em dash is the one that was asked to go."""
    guilty = {k: v for k, v in _customer_strings(lang).items() if "—" in v}
    assert not guilty, f"long dash in: {sorted(guilty)}"


def test_ukrainian_never_slips_into_the_formal_you():
    """«Ми звертаємось до людини як до рівної, на "ти"» (Tone of voice, p. 199).
    One «Ваш» is enough to make the whole screen sound like a bank."""
    formal = re.compile(r"\b(Ви|ви|Ваш|ваш|Вам|вам|Вас|вас)\b")
    guilty = {}
    for key, value in _customer_strings("uk").items():
        # «вами», «вами» inside a word are fine; only whole words count.
        if formal.search(value):
            guilty[key] = value
    assert not guilty, f"formal address in: {sorted(guilty)}"
