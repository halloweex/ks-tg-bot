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


# --- strings that outlive the screens they were written for -------------------

# Custom emoji ids kept as a palette rather than as copy: the payment page names
# the methods it accepts, and the day it names one more it is one tag away.
# core/texts.py explains the choice; they are ids, not sentences, so nothing
# reads them and nothing should.
_KEPT_ON_PURPOSE = {"VISA", "MASTERCARD", "MONOBANK", "PRIVAT24", "NOVA_POSHTA",
                    "INSTAGRAM"}


def test_no_string_outlives_its_screen():
    """Six constants were found dead at once — buttons labelled with a number,
    and the legends that existed to explain what the number meant, all replaced
    when the digest started labelling buttons with dates. Nothing noticed for
    weeks, because an unused constant is not an unused name: pyflakes sees a
    module attribute and says nothing.

    A dead string is not harmless. It is read as current when somebody comes
    looking for the wording of a screen, and translated again in every language
    added after it died."""
    import pathlib
    import re

    from core import texts

    repo = pathlib.Path(__file__).resolve().parent.parent
    body = []
    for path in (list((repo / "bot").rglob("*.py"))
                 + list((repo / "core").rglob("*.py"))):
        for line in path.read_text().splitlines():
            # The declarations themselves, in either table, are not uses.
            if re.match(r"^[A-Z_]+\s*=", line) or re.match(r'\s*"[A-Z_]+":', line):
                continue
            body.append(line)
    source = "\n".join(body)

    dead = sorted(
        name for name in dir(texts)
        if name.isupper() and name not in _KEPT_ON_PURPOSE
        and isinstance(getattr(texts, name), str)
        and not re.search(rf"\b{name}\b", source)
    )
    assert not dead, f"no screen says these any more: {dead}"
