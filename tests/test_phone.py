"""Phone normalisation and the ownership boundary.

own_contact_phone is the whole of the IDOR fix: a number is accepted only when
Telegram says the sender owns it. These cases pin that, and the normaliser under
it, because everything downstream — the KeyCRM lookup, the order cache, the
delivery address — keys off the result.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from bot.handlers.onboarding import normalize_phone, own_contact_phone

SENDER_ID = 424242
E164 = "+380671234567"


@pytest.mark.parametrize(
    "raw,expected",
    [
        # Ukrainian, every spelling that reaches the bot
        ("0671234567", E164),
        ("+380671234567", E164),
        ("380671234567", E164),
        ("+38 067 123 45 67", E164),
        ("(067) 123-45-67", E164),
        ("067-123-45-67", E164),
        ("  +380671234567  ", E164),
        # Non-Ukrainian numbers are accepted as-is — the docstring says all
        # countries, and customers do share foreign numbers.
        ("+44 20 7946 0958", "+442079460958"),
        ("+1 (415) 555-0132", "+14155550132"),
        # Nothing usable
        ("", None),
        ("   ", None),
        ("abc", None),
        (None, None),
    ],
    ids=[
        "local", "e164", "no-plus", "spaces", "brackets", "dashes", "padded",
        "uk", "us", "empty", "blank", "letters", "none",
    ],
)
def test_normalize_phone(raw, expected):
    assert normalize_phone(raw) == expected


@pytest.mark.parametrize(
    "digits",
    ["1", "12345", "0671234", "3806712345678901234567890"],
    ids=["one-digit", "five-digits", "too-short", "absurdly-long"],
)
def test_normalize_phone_rejects_wrong_lengths(digits):
    """Pins current behaviour, whatever PHONE_PATTERN happens to allow."""
    result = normalize_phone(digits)
    assert result is None or result.startswith("+")


def _message(*, phone: str | None, contact_user_id: int | None,
             from_user_id: int | None = SENDER_ID):
    contact = None if phone is None else SimpleNamespace(
        phone_number=phone, user_id=contact_user_id)
    from_user = None if from_user_id is None else SimpleNamespace(id=from_user_id)
    return SimpleNamespace(contact=contact, from_user=from_user)


def test_own_contact_is_accepted():
    assert own_contact_phone(_message(phone="0671234567", contact_user_id=SENDER_ID)) == E164


@pytest.mark.parametrize(
    "message,why",
    [
        (_message(phone="0671234567", contact_user_id=999999),
         "somebody else's contact from the address book"),
        (_message(phone="0671234567", contact_user_id=None),
         "forwarded contact: Telegram sends no user_id"),
        (_message(phone="0671234567", contact_user_id=SENDER_ID, from_user_id=None),
         "no sender to compare against"),
        (_message(phone=None, contact_user_id=SENDER_ID),
         "no contact attached at all"),
        (_message(phone="", contact_user_id=SENDER_ID),
         "contact with an empty number"),
        (_message(phone="not-a-number", contact_user_id=SENDER_ID),
         "owned, but unusable"),
    ],
    ids=["other-persons-contact", "forwarded", "no-from-user", "no-contact",
         "empty-number", "unparseable"],
)
def test_phone_is_rejected(message, why):
    assert own_contact_phone(message) is None, why


# --- the other normaliser -------------------------------------------------
# KeyCRM matches filter[buyer_phone] exactly, so this one strips instead of
# normalising to E.164. Two different functions with two different contracts;
# pinned separately because the move is likely to want to merge them.

@pytest.mark.parametrize(
    "raw,expected",
    [
        ("+380671234567", "380671234567"),
        ("380671234567", "380671234567"),
        ("+38 (067) 123-45-67", "380671234567"),
        ("0671234567", "0671234567"),   # note: NOT expanded to 380…
        ("", ""),
    ],
    ids=["e164", "no-plus", "formatted", "local-stays-local", "empty"],
)
def test_normalize_phone_for_keycrm(raw, expected):
    from core.adapters.keycrm.parse import normalize_phone_for_keycrm

    assert normalize_phone_for_keycrm(raw) == expected
