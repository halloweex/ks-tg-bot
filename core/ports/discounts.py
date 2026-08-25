"""Somewhere a promo code can be brought into existence.

The bot has never created a discount. It hands out a link that applies one, and
the codes behind those links are written by a person in the shop's admin — see
`config.yaml`, where an empty code means the offer says a manager will send it.

That is the right shape for an offer shown to everybody: one code, one line of
config, no moving parts. It is the wrong shape for a reward owed to one person,
which is what this port is for. A referral is earned once, by one customer, and
a code they can pass to a friend is a discount the shop did not agree to give.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class DiscountCodes(Protocol):
    """Issues a single-use percentage code, or admits it could not."""

    async def issue_percentage(self, percent: int, *, title: str) -> str | None:
        """Create a code worth `percent` off, usable once, and return it.

        `title` is what a person sees in the shop's admin beside the code, so
        it should say who it was for and why: a row nobody can explain is a row
        nobody dares delete.

        None means the code was not created, for any reason at all. The caller
        must treat that as "no code" and fall back to whatever it does when
        none is configured — never as a code that might work. A promise made on
        a code that does not exist is worse than the slower promise it replaced.
        """
        ...
