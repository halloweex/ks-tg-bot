"""Outside working hours the bot stops implying someone is reading.

«Передали менеджеру — відповімо тут» is true at three in the afternoon and a
promise nobody is awake to keep at three in the morning. The message still goes
through either way — what changes is what the customer is told to expect.
"""
from __future__ import annotations

from datetime import datetime, time, timezone
from types import SimpleNamespace

import pytest

from bot.handlers.support import forwarded_confirmation, support_prompt
from core.config import AppConfig
from core.domain.quiet import within_hours
from core.i18n import SUPPORTED, Texts

T = Texts("uk")
OPEN, CLOSED = time(9, 0), time(21, 0)


def _utc(hour: int, minute: int = 0) -> datetime:
    """Kyiv runs three hours ahead in August, which these hours are dated in."""
    return datetime(2026, 8, 25, hour, minute, tzinfo=timezone.utc)


# --- the window itself ------------------------------------------------------

@pytest.mark.parametrize(
    "utc_hour,inside,why",
    [
        (6, True, "09:00 Kyiv — the minute it opens"),
        (12, True, "15:00 Kyiv — the middle of the day"),
        (17, True, "20:00 Kyiv — the last hour"),
        (18, False, "21:00 Kyiv — the minute it closes"),
        (23, False, "02:00 Kyiv — the case this exists for"),
        (5, False, "08:00 Kyiv — an hour early"),
    ],
)
def test_the_window_is_the_shops_clock_not_the_servers(utc_hour, inside, why):
    """The VPS runs on UTC and the customers are in Ukraine — the same reason
    quiet hours already convert."""
    assert within_hours(OPEN, CLOSED, now=_utc(utc_hour)) is inside, why


def test_a_window_that_ends_before_it_starts_wraps_midnight():
    assert within_hours(time(21, 0), time(9, 0), now=_utc(23)) is True
    assert within_hours(time(21, 0), time(9, 0), now=_utc(12)) is False


# --- what the customer is told ---------------------------------------------

def _config(start: str = "09:00", end: str = "21:00") -> AppConfig:
    return SimpleNamespace(
        support_hours_from=start, support_hours_to=end,
        support_window=AppConfig.support_window.fget(
            SimpleNamespace(support_hours_from=start, support_hours_to=end)),
    )


@pytest.mark.parametrize("lang", sorted(SUPPORTED))
def test_off_hours_names_the_hour(lang):
    said = forwarded_confirmation(Texts(lang), _config(), now=_utc(23))
    assert "09:00" in said, "a wait with an hour on it is a wait, not silence"


def test_working_hours_keep_the_short_answer():
    assert forwarded_confirmation(T, _config(), now=_utc(12)) == T.MSG_SUPPORT_FORWARDED


def test_no_hours_configured_promises_nothing():
    """The behaviour before the setting existed, and the one an empty config
    must keep: the bot would rather say nothing about timing than invent it."""
    for now in (_utc(3), _utc(12), _utc(23)):
        assert forwarded_confirmation(T, _config("", ""), now=now) == T.MSG_SUPPORT_FORWARDED


def test_half_a_window_is_no_window():
    """One end configured is a promise with a hole in it."""
    assert _config("09:00", "").support_window is None
    assert _config("", "21:00").support_window is None


def test_nonsense_hours_do_not_reach_a_customer():
    assert _config("завтра", "ніколи").support_window is None


# --- the hour is named once, before she types --------------------------------


def test_the_prompt_names_the_hour_so_she_learns_it_before_writing():
    """The defect was the order, not the words. She opened support at 23:40,
    read «розкажи, що турбує», wrote out the whole problem, and learnt only on
    send that nobody would read it until morning."""
    said, named = support_prompt(T, _config(), now=_utc(23))
    assert "09:00" in said
    assert named is True


def test_inside_working_hours_the_prompt_is_the_short_one():
    said, named = support_prompt(T, _config(), now=_utc(12))
    assert said == T.MSG_SUPPORT_PROMPT
    assert named is False


def test_with_no_hours_configured_the_prompt_invents_nothing():
    """The same rule the confirmation already follows: the bot would rather say
    nothing about timing than make a time up."""
    said, named = support_prompt(T, _config("", ""), now=_utc(23))
    assert said == T.MSG_SUPPORT_PROMPT
    assert named is False


def test_the_hour_is_not_named_twice_in_one_minute():
    """If the prompt said «будемо на зв'язку з 09:00», the confirmation a minute
    later says the ordinary line. Two "nobody is here" messages in one minute is
    worse than one."""
    assert forwarded_confirmation(
        T, _config(), now=_utc(23), hours_named=True) == T.MSG_SUPPORT_FORWARDED


def test_a_customer_who_was_never_told_still_gets_told():
    """The flag defaults to False, which is what keeps every existing caller and
    every test above behaving as it did."""
    assert "09:00" in forwarded_confirmation(T, _config(), now=_utc(23))


@pytest.mark.parametrize("lang", sorted(SUPPORTED))
def test_both_languages_have_the_off_hours_prompt(lang):
    """Texts falls through to the Ukrainian module for a missing key, so a
    forgotten English entry hands an English-speaking customer a Ukrainian
    screen instead of raising."""
    said, _named = support_prompt(Texts(lang), _config(), now=_utc(23))
    assert said != Texts("uk").MSG_SUPPORT_PROMPT_OFF_HOURS.format(time="09:00") or lang == "uk"
    assert "09:00" in said
