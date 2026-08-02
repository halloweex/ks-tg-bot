"""A verified phone cannot be made out of a string.

docs/architecture.md §11 asks for the ownership invariant to hold in the runtime
rather than by convention, and §3 names the mechanism: a frozen dataclass with a
required key field. Until now the rule was that everyone downstream trusted the
one function that did the check — which is exactly the kind of guarantee that
survives until someone adds a second caller in a hurry.

These are the cases that make the type worth the trouble, plus the holes it
deliberately does not close.
"""
from __future__ import annotations

import dataclasses

import pytest

from core.domain.phone import (PHONE_PATTERN, VerifiedPhone, normalize_phone,
                               verified_phone)

NUMBER = "+380671234567"
SENDER = 424242


def _verified() -> VerifiedPhone:
    return verified_phone(raw_number="0671234567", contact_user_id=SENDER,
                          sender_user_id=SENDER)


# --- the boundary itself ---------------------------------------------------

def test_a_contact_the_sender_owns_is_verified():
    phone = _verified()
    assert phone is not None
    assert phone.e164 == NUMBER


@pytest.mark.parametrize(
    "kwargs,why",
    [
        ({"contact_user_id": 999999}, "somebody else's contact from the address book"),
        ({"contact_user_id": None}, "forwarded contact: Telegram sends no user_id"),
        ({"sender_user_id": None}, "no sender to compare against"),
        ({"raw_number": None}, "no contact attached at all"),
        ({"raw_number": ""}, "contact with an empty number"),
        ({"raw_number": "not-a-number"}, "owned, but unusable"),
    ],
    ids=["other-persons-contact", "forwarded", "no-sender", "no-number",
         "empty-number", "unparseable"],
)
def test_ownership_that_is_not_proven_yields_nothing(kwargs, why):
    args = {"raw_number": "0671234567", "contact_user_id": SENDER,
            "sender_user_id": SENDER, **kwargs}
    assert verified_phone(**args) is None, why


# --- the type as a barrier -------------------------------------------------

def test_a_bare_string_does_not_become_a_verified_phone():
    """The whole point: a caller who has a number and needs a VerifiedPhone
    cannot simply wrap it."""
    with pytest.raises(TypeError, match="cannot be built from a bare string"):
        VerifiedPhone(NUMBER, object())


def test_the_proof_field_has_no_default():
    """A default would make VerifiedPhone(number) work, and the barrier with it."""
    fields = {f.name: f for f in dataclasses.fields(VerifiedPhone)}
    assert fields["proof"].default is dataclasses.MISSING
    assert fields["proof"].default_factory is dataclasses.MISSING


def test_it_cannot_be_reassigned():
    phone = _verified()
    with pytest.raises(dataclasses.FrozenInstanceError):
        phone.e164 = "+380000000000"


def test_the_number_it_carries_is_always_e164():
    """Anything that got past the constructor is in one shape, so nothing
    downstream has to normalise again — or forget to."""
    assert PHONE_PATTERN.match(_verified().e164)


def test_it_does_not_print_the_number_in_a_traceback():
    """repr lands in tracebacks, and the log masker works on the sink, not on
    the repr of every object that passes through it."""
    phone = _verified()
    assert NUMBER not in repr(phone)
    assert repr(phone) == "VerifiedPhone(...4567)"
    # str() is the deliberate exception: code that asks for the number in so
    # many words gets it.
    assert str(phone) == NUMBER


def test_the_holes_are_where_the_docstring_says_they_are():
    """Named rather than hidden. A guarantee whose limits nobody wrote down
    gets trusted further than it deserves — object.__setattr__ defeats frozen
    for every frozen dataclass, and this one is no exception."""
    phone = _verified()
    object.__setattr__(phone, "e164", "+380000000000")
    assert phone.e164 == "+380000000000"

    assert "object.__setattr__" in (__import__(
        "core.domain.phone", fromlist=["x"]).__doc__ or "")


# --- normalisation, which is not verification ------------------------------

@pytest.mark.parametrize(
    "raw,expected",
    [
        ("0671234567", NUMBER),
        ("+380671234567", NUMBER),
        ("380671234567", NUMBER),
        ("+38 067 123 45 67", NUMBER),
        ("(067) 123-45-67", NUMBER),
        ("+44 20 7946 0958", "+442079460958"),
        ("", None),
        ("abc", None),
        (None, None),
    ],
    ids=["local", "e164", "no-plus", "spaces", "brackets", "uk", "empty",
         "letters", "none"],
)
def test_normalize_phone(raw, expected):
    """Still a plain function returning a plain string: it says a number looks
    like a number, which is a different claim from who it belongs to."""
    assert normalize_phone(raw) == expected
