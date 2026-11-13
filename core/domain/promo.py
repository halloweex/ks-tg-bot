"""A promo code as the shop's admin describes it, and whether to hand it out.

The code is written by a person in Shopify, marketing, with every setting the
admin has; the bot only hands it out. What the bot can do is look before it
hands: a typo, a code that ended last week or one already used up would
otherwise go to every recipient of a broadcast, each of whom finds out at the
checkout. The look is advice and never a gate: staff decide what goes out
(bot/handlers/broadcast.py), because a code can be created a minute after it
is typed, and the shop can be unreachable on a perfectly good day.

Pure on purpose: the adapter fills a `Promo` in, the rule below reads it.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

OK = "ok"
MISSING = "missing"
EXPIRED = "expired"
SCHEDULED = "scheduled"
USED_UP = "used_up"
INACTIVE = "inactive"


@dataclass(frozen=True)
class Promo:
    code: str
    title: str = ""
    # As Shopify says it: ACTIVE, EXPIRED or SCHEDULED.
    status: str = ""
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    usage_limit: int | None = None
    used: int | None = None
    # 10.0 for ten per cent off; or a fixed amount, «200 UAH».
    percent: float | None = None
    amount: str | None = None


def verdict(promo: Promo | None, now: datetime) -> str:
    """Whether a customer handed this code now would get the discount.

    The dates are read as well as the status, because the status is Shopify's
    view at the moment it was asked and a broadcast is composed a while before
    it is sent.
    """
    if promo is None:
        return MISSING
    if promo.status == "EXPIRED" or (promo.ends_at is not None and promo.ends_at <= now):
        return EXPIRED
    if promo.status == "SCHEDULED" or (promo.starts_at is not None
                                       and promo.starts_at > now):
        return SCHEDULED
    if (promo.usage_limit is not None and promo.used is not None
            and promo.used >= promo.usage_limit):
        return USED_UP
    if promo.status != "ACTIVE":
        return INACTIVE
    return OK
