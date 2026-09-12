"""The rules that turn rows of `buyer_gender` into one form per chat."""
from __future__ import annotations

import pytest

from core.domain.gender import DEFAULT, Gender, form, resolve


def test_nothing_stored_reads_as_the_default_and_the_default_is_feminine():
    """Every chat is in this state the moment the column ships, so it has to
    render what the bot rendered before — not the unmarked form."""
    assert form(None) is Gender.F
    assert form("") is Gender.F
    assert DEFAULT is Gender.F


@pytest.mark.parametrize("stored,expected", [
    ("f", Gender.F), ("m", Gender.M), ("u", Gender.UNKNOWN),
    ("M", Gender.M), (" f ", Gender.F),
])
def test_a_stored_value_decodes(stored, expected):
    assert form(stored) is expected


def test_an_unreadable_value_does_not_break_a_screen():
    """A column written by some later version must not be able to raise inside
    the middleware that builds `t` for every single update."""
    assert form("nonbinary-but-not-in-this-enum") is DEFAULT
    assert form("ж") is DEFAULT


def test_one_card_decides():
    assert resolve(["f"]) is Gender.F
    assert resolve(["m"]) is Gender.M


def test_cards_that_agree_decide():
    assert resolve(["m", "m", None]) is Gender.M


def test_cards_that_disagree_stop_the_guessing():
    """Two buyer cards behind one number are two people, and nothing here can
    tell which of them is typing."""
    assert resolve(["f", "m"]) is Gender.UNKNOWN


def test_the_classifier_refusing_is_unknown_and_so_is_having_no_cards():
    assert resolve([None]) is Gender.UNKNOWN
    assert resolve([]) is Gender.UNKNOWN
    assert resolve(["", "  "]) is Gender.UNKNOWN


def test_a_value_the_table_should_not_hold_is_not_taken_as_an_answer():
    """'x' is neither 'f' nor 'm'. Counting it as a third opinion would make a
    typo in somebody else's pipeline change the wording here."""
    assert resolve(["x"]) is Gender.UNKNOWN
    assert resolve(["f", "x"]) is Gender.F
