"""The three forms of the Ukrainian copy, checked against each other.

`core/i18n.py` holds two override tables — masculine and unmarked — under the
same keys as `core/texts.py`. Nothing about that arrangement is enforced by the
language: a typo in a key is a screen that silently keeps the feminine wording,
a missing `{placeholder}` is a brand name that disappears from a greeting, and an
extra one is a `KeyError` in front of a customer. These are the tests that make
the arrangement hold.
"""
from __future__ import annotations

import re
import string

import pytest

from core import texts
from core.domain.gender import Gender
from core.i18n import (EN, FEMININE, FORMS, MASCULINE, NEUTRAL, UK_MASCULINE,
                       UK_NEUTRAL, Texts, variants)

TABLES = {MASCULINE: UK_MASCULINE, NEUTRAL: UK_NEUTRAL}


def _fields(value: str) -> set[str]:
    """The {named} placeholders in a string, ignoring literal text."""
    return {name for _, name, _, _ in string.Formatter().parse(value) if name}


def test_the_two_spellings_of_a_form_agree():
    """`core.i18n` may not import `core.domain` (i18n-is-a-leaf), so the three
    letters are written twice. This is the seam where that stops being safe."""
    assert (FEMININE, MASCULINE, NEUTRAL) == (
        Gender.F.value, Gender.M.value, Gender.UNKNOWN.value)
    assert set(FORMS) == {g.value for g in Gender}


@pytest.mark.parametrize("form", sorted(TABLES))
def test_every_gendered_key_is_a_real_key(form):
    """A key that matches nothing in texts.py is an override that never fires —
    and it would look, to anyone reading the table, like a screen that has a
    masculine wording when it does not."""
    unknown = sorted(key for key in TABLES[form] if not hasattr(texts, key))
    assert not unknown, f"{form}: no such string in core/texts.py: {unknown}"


@pytest.mark.parametrize("form", sorted(TABLES))
def test_no_override_repeats_the_feminine_wording(form):
    """An override identical to the original is either a copy-paste that forgot
    to change the verb, or a row that should not be in the table."""
    same = sorted(key for key, value in TABLES[form].items()
                  if value == getattr(texts, key))
    assert not same, f"{form}: identical to the feminine form: {same}"


@pytest.mark.parametrize("form", sorted(TABLES))
def test_every_form_interpolates_the_same_things(form):
    """`.format(**kwargs)` is called by the handler, which knows one set of
    arguments. A form that asks for a field the caller does not pass raises in
    front of the customer; one that drops a field loses the value silently."""
    wrong = {key: (_fields(getattr(texts, key)), _fields(value))
             for key, value in TABLES[form].items()
             if _fields(value) != _fields(getattr(texts, key))}
    assert not wrong, f"{form}: placeholders differ: {wrong}"


def test_the_feminine_form_is_exactly_what_shipped_before():
    """The default form must be byte-identical to the module, in both languages.
    Every chat is in it until something decides otherwise, so any difference
    here is a change to what today's customers read."""
    for lang, table in (("uk", {}), ("en", EN)):
        t = Texts(lang, FEMININE)
        for key in TABLES[MASCULINE] | TABLES[NEUTRAL].keys():
            expected = table.get(key, getattr(texts, key))
            assert getattr(t, key) == expected, f"{lang}/{key}"


def test_english_ignores_the_form():
    """English marks no gender, so it has no tables and must fall through to EN
    rather than to the Ukrainian module."""
    for form in FORMS:
        t = Texts("en", form)
        assert t.GREETING == EN["GREETING"]
        assert t.BTN_INVITE == EN["BTN_INVITE"]


def test_a_button_is_matched_in_every_form_it_can_be_sent_in():
    """BTN_INVITE is a reply-keyboard key: the router matches the text Telegram
    sends back. A form missing from variants() is a button that does nothing."""
    every = variants("BTN_INVITE")
    for lang in ("uk", "en"):
        for form in FORMS:
            assert getattr(Texts(lang, form), "BTN_INVITE") in every


# --- the inventory ----------------------------------------------------------
#
# A feminine past tense is a claim about the reader, and it is the kind of thing
# that arrives one string at a time. This scans for the ending and demands that
# anything it finds is either overridden in both tables or named below as not
# being about the reader at all.
_FEMININE_ENDING = re.compile(r"\b\w+(?:ла|лась|лася|на|ана|ена|іна)\b")

#: Words the scanner matches that say nothing about the customer's gender. Each
#: is a real word of Ukrainian that happens to end the same way.
_NOT_THE_READER = {
    "Очікувана",   # дата, feminine because «дата» is
    "Вона",        # знижка, in MSG_FIRST_ORDER_IN_LINK
    "Потрібна",    # людина
    "людина",
    "можна",
    "вручну",
    "одна",
    "вона",
}

#: Strings read by a manager or by us, never by a customer — the operator
#: surface is not written in the brand's voice and is not gendered.
_NOT_CUSTOMER_FACING = {
    "MSG_SUPPORT_ADMIN_NOTE", "MSG_SUPPORT_REPLY_INSTRUCTION",
    "MSG_SUPPORT_NO_REPLY_TARGET", "MSG_DISCOUNT_ADMIN", "MSG_FAVOURITE_LINE",
    "MSG_REFERRAL_ADMIN_SUMMARY", "MSG_CUSTOMER_UNKNOWN", "MSG_SUPPORT_FOCUS_ON",
    "MSG_SUPPORT_FOCUS_OFF", "MSG_BROADCAST_PROMPT", "MSG_BROADCAST_CONFIRM",
    "MSG_BROADCAST_STARTED", "MSG_BROADCAST_COMPLETE", "MSG_BROADCAST_CANCELLED",
    "MSG_BROADCAST_NO_RECIPIENTS", "MSG_BROADCAST_LOOKS_LIKE_A_COMMAND",
}

#: The bot talking about itself. The brand persona is Анна, so the feminine here
#: is hers and stays in all three forms — see core/i18n.py.
_THE_BOT_ITSELF = {"MSG_WELCOME_BACK_NAME"}


def test_no_feminine_string_is_left_without_the_other_two_forms():
    uncovered = {}
    for key in dir(texts):
        if not key.isupper() or key in _NOT_CUSTOMER_FACING or key in _THE_BOT_ITSELF:
            continue
        value = getattr(texts, key)
        if not isinstance(value, str):
            continue
        found = [word for word in _FEMININE_ENDING.findall(value)
                 if word not in _NOT_THE_READER]
        if found and not all(key in TABLES[form] for form in TABLES):
            uncovered[key] = found
    assert not uncovered, (
        "feminine wording with no masculine/unmarked form: "
        f"{ {k: v for k, v in sorted(uncovered.items())} }")
