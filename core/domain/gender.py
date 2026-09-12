"""Which grammatical form a customer's own screens are written in.

Ukrainian marks the addressee's gender in the past tense, so "що ти вже
купувала" is not a style choice — it is a claim about the person reading it, and
it is wrong for 777 of the 20 145 buyers this shop has. The claim is not the
bot's to make: `buyer_gender` (DuckDB) / `app.buyer_gender` (Postgres) holds one
row per buyer, decided by a classifier whose rules, version and human overrides
live with the table and not here.

**Three forms, not two.** `gender IS NULL` in that table is the classifier
refusing to decide, which is a different thing from a buyer it has never seen —
and both are different from an answer. So the bot speaks in three forms:
feminine, masculine, and one written without marking gender at all, which is
what an unknown person gets.

**What NULL means here, and why it is not the neutral form.** A chat with
nothing stored has not been asked yet: no source configured, or a refresh that
has not run. That is the state every chat is in the moment this ships, and it
must read exactly as the bot read before — feminine, which is how these strings
were written and right for 94.8% of the base. Asked-and-unknown is stored as
`'u'` precisely so the two cannot be confused: one is a fact about the person,
the other is a fact about our pipeline.

**No second confidence gate.** The table carries `confidence`, and it is
tempting to require 'certain'. The classifier already expresses its doubt by
writing NULL — re-gating here would be a threshold nobody measured, applied to
somebody else's measurement. Any non-NULL answer is taken as an answer, human
overrides included, since an override arrives as the value itself.
"""
from __future__ import annotations

from enum import Enum
from typing import Iterable


class Gender(str, Enum):
    """The three forms a customer-facing string can be written in.

    `str`-valued because these are also what goes in the column: 'f', 'm', and
    'u' for asked-and-unknown. A never-asked chat stores NULL, which `form()`
    reads as the default rather than as UNKNOWN.
    """

    F = "f"
    M = "m"
    UNKNOWN = "u"


#: What a chat gets before anything has decided otherwise. Feminine and not
#: neutral on purpose — see the module docstring.
DEFAULT = Gender.F


def form(stored: str | None) -> Gender:
    """Decode the value the bot keeps for one chat.

    Anything unexpected reads as the default: a column written by a future
    version of the refresh must not be able to make a screen fail to render.
    """
    if not stored:
        return DEFAULT
    try:
        return Gender(stored.strip().lower())
    except ValueError:
        return DEFAULT


def resolve(genders: Iterable[str | None]) -> Gender:
    """One form for one chat, from the buyer cards it turned out to be.

    A number can belong to several buyer cards — 203 of them in this CRM — so a
    chat can hold more than one row of the table. Cards that agree decide;
    cards that disagree are two different people behind one number, and the bot
    has no way to tell which one is typing, so it stops guessing and speaks
    without marking gender.

    An empty iterable is the same answer: it means the source was asked and had
    nothing, which is exactly what "unknown" is for.
    """
    answers = {value.strip().lower() for value in genders if value and value.strip()}
    known = {answer for answer in answers if answer in (Gender.F.value, Gender.M.value)}
    if len(known) != 1:
        return Gender.UNKNOWN
    return Gender(known.pop())
