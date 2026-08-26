"""What a birthday is, as this bot stores it: a month and a day and no year.

No year on purpose. Telegram supplies one only sometimes, the greeting does not
need it, and an age is personal data the shop has no use for — so the column
holds "MM-DD" and nothing more.

The empty string is a value here rather than an absence: it records that
Telegram was asked and showed no date, which is most people, and it is the only
reason the sweep is cheap. That makes "" a perfectly ordinary thing to find in
the column and a catastrophic thing to search for — a query for it matches
nearly every customer.
"""
from __future__ import annotations

import re
from datetime import date
from typing import Final

_MONTH_DAY: Final = re.compile(r"^(0[1-9]|1[0-2])-(0[1-9]|[12][0-9]|3[01])$")


def month_day(day: date) -> str:
    """The stored spelling of a date, without its year."""
    return day.strftime("%m-%d")


def is_month_day(value: str) -> bool:
    """True for "MM-DD", false for "" and for anything else.

    Deliberately rejects the empty string, which is the whole point: "" is what
    the column holds for somebody with no visible date, so searching for it
    would return everybody who has ever been asked. 02-30 is accepted — it is a
    day nobody has, not a malformed one, and the calendar's business is not this
    regex's.
    """
    return bool(_MONTH_DAY.match(value))
