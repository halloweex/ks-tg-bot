"""A phone number, and proof that the person handing it over owns it.

This is the IDOR fix expressed as a type. Telegram sets `contact.user_id` to the
sharer's own id only when they tap the request-contact button to share *their*
number; a forwarded contact, or one picked out of an address book, carries a
different id or none. Accepting a number without that check would let anyone
bind a stranger's number to their own chat and read that person's orders and
delivery address.

Until now the rule was held by convention — one function did the check, and
everything downstream took a bare `str` and trusted whoever called it.
docs/architecture.md §11 asks for the invariant to hold in the runtime instead,
and §3 spells out how: a frozen dataclass with a required key field, so a plain
string does not assemble into one by accident.

**The remaining holes, named rather than hidden**, because a guarantee whose
limits are undocumented gets trusted further than it deserves:

* `object.__setattr__` defeats frozen, as it does for every frozen dataclass;
* anything in *this* module can mint one, so the module is deliberately small
  and has exactly one constructor;
* `dataclasses.replace` copies the proof along with the rest.

None of these happen by accident, which is the point: the type stops the honest
mistake — a number that came from a text message, a forwarded card, or a caller
who had a string and needed a phone.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

# E.164: + followed by 7-15 digits (covers all international numbers)
PHONE_PATTERN: Final = re.compile(r"^\+\d{7,15}$")

# The key from §3. Module-private and compared by identity, so a caller outside
# cannot name it and an equal-looking value will not do.
_OWNERSHIP_PROVEN: Final = object()


def normalize_phone(raw: str | None) -> str | None:
    """Normalize any input to an E.164 number (+digits). Accepts all countries.

    Ukrainian local formats are handled for convenience:
      380XXXXXXXXX (12 digits) -> +380XXXXXXXXX
      0XXXXXXXXX   (10 digits) -> +380XXXXXXXXX
    Anything else becomes '+' + digits (so a US/UK/DE/etc. number typed with or
    without a leading '+' is accepted). Returns None if the result isn't a valid
    E.164 number.

    Normalising is not verifying: this says a string looks like a number, not
    that it belongs to the person who sent it. That is verified_phone below.
    """
    digits = re.sub(r"\D", "", raw or "")
    if not digits:
        return None
    if digits.startswith("380") and len(digits) == 12:
        phone = "+" + digits
    elif digits.startswith("0") and len(digits) == 10:
        phone = "+38" + digits
    else:
        phone = "+" + digits
    return phone if PHONE_PATTERN.match(phone) else None


@dataclass(frozen=True)
class VerifiedPhone:
    """A number Telegram confirmed belongs to the account that sent it.

    Not constructible from outside this module: `proof` has no default, and the
    only value it accepts is one this module never exports.
    """

    e164: str
    proof: object

    def __post_init__(self) -> None:
        if self.proof is not _OWNERSHIP_PROVEN:
            raise TypeError(
                "VerifiedPhone cannot be built from a bare string — a number is "
                "verified only through verified_phone(), which requires the "
                "Telegram contact's own user id. See docs/architecture.md §11."
            )
        if not PHONE_PATTERN.match(self.e164):
            raise ValueError(f"not an E.164 number: {self.e164!r}")

    def __str__(self) -> str:
        return self.e164

    def __repr__(self) -> str:
        """Masked: this ends up in tracebacks, and bot/logs.py masks the sink,
        not the repr of every object that passes through it."""
        return f"VerifiedPhone(...{self.e164[-4:]})"


def verified_phone(
    *,
    raw_number: str | None,
    contact_user_id: int | None,
    sender_user_id: int | None,
) -> VerifiedPhone | None:
    """The one way to make a VerifiedPhone. None if ownership is not proven.

    Takes the three facts rather than a Telegram message, so the rule lives here
    and can be tested without aiogram; pulling those facts out of a Message is
    the handler's job and the only part that belongs to the transport.
    """
    if not raw_number:
        return None
    if contact_user_id is None or sender_user_id is None:
        return None
    if contact_user_id != sender_user_id:
        return None
    normalized = normalize_phone(raw_number)
    if normalized is None:
        return None
    return VerifiedPhone(normalized, _OWNERSHIP_PROVEN)
