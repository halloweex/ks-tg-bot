"""Who a broadcast goes to: the filters a person picks, and the rule applying them.

Every filter is a short list of choices the picker steps through, and the first
choice of each means "no filter". Conditions combine with AND — a recipient has
to pass every one of them — because that is how "regulars who have not ordered
in three months" is said out loud.

Pure on purpose. The repository gathers one `Profile` per chat that may be
written to and asks `matches`, so the rule is tested without a database and
moves to Postgres without being rewritten.

**What counts as a purchase** is decided by whoever builds the profile, and it is
the orders screen's rule: an order that was not cancelled and is not a demo,
once however many systems report it. Nothing here re-derives it.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, fields, replace
from datetime import datetime, timedelta

# Purchases: inclusive bounds, None for "no upper bound".
PURCHASES: dict[str, tuple[int, int | None]] = {
    "any": (0, None),
    "none": (0, 0),
    "1": (1, 1),
    "2+": (2, None),
    "3+": (3, None),
    "5+": (5, None),
}

# How long ago the latest purchase was, in days: (after, up to). None for no
# limit on that side. A buyer with no purchases matches none but "any".
LAST_ORDER: dict[str, tuple[int, int | None] | None] = {
    "any": None,
    "30": (0, 30),
    "30-90": (30, 90),
    "90+": (90, None),
}

# New in the bot within N days, or not seen in it for 30. "Seen" is any event
# that carries the person's chat — those are only ever written by something the
# person did (core/ports/analytics.py), never by the bot on its own.
ACTIVITY = ("any", "new7", "new30", "idle30")

# Subscribed to a restock notice. Narrowed by `waiting_for` when one is given.
WAITING = ("any", "yes")

# The filters the picker steps through by tapping. The two word filters are not
# here: their value is typed, not chosen.
STEPPED = {
    "purchases": tuple(PURCHASES),
    "last_order": tuple(LAST_ORDER),
    "activity": ACTIVITY,
}

WORD_LIMIT = 40


@dataclass(frozen=True)
class Audience:
    purchases: str = "any"
    last_order: str = "any"
    # A word from a product or brand name, matched against what was bought.
    bought: str = ""
    waiting: str = "any"
    waiting_for: str = ""
    activity: str = "any"

    @property
    def everyone(self) -> bool:
        return self == Audience()

    def stepped(self, name: str) -> Audience:
        """The same audience with one filter moved to its next choice."""
        choices = STEPPED[name]
        current = getattr(self, name)
        following = choices[(choices.index(current) + 1) % len(choices)]
        return replace(self, **{name: following})

    def as_dict(self) -> dict[str, str]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict | None) -> Audience:
        """Tolerant on purpose: this comes back out of stored conversation state.

        A value that is no longer a choice — renamed in a later release while
        somebody was halfway through the picker — reads as no filter. Falling
        back to *wider* is the safe direction only because the picker shows the
        count before anything is sent.
        """
        raw = raw or {}
        allowed = {"purchases": PURCHASES, "last_order": LAST_ORDER,
                   "activity": ACTIVITY, "waiting": WAITING}
        values = {}
        for field in fields(cls):
            value = raw.get(field.name, field.default)
            if not isinstance(value, str):
                value = field.default
            if field.name in allowed and value not in allowed[field.name]:
                value = field.default
            values[field.name] = value.strip()[:WORD_LIMIT]
        return cls(**values)


@dataclass(frozen=True)
class Purchase:
    at: datetime | None
    products: tuple[str, ...] = ()


@dataclass(frozen=True)
class Profile:
    """What the filters are allowed to know about one recipient."""

    chat_id: int
    joined_at: datetime | None = None
    last_seen: datetime | None = None
    purchases: tuple[Purchase, ...] = ()
    waiting_for: tuple[str, ...] = ()


def matches(audience: Audience, profile: Profile, now: datetime) -> bool:
    low, high = PURCHASES[audience.purchases]
    count = len(profile.purchases)
    if count < low or (high is not None and count > high):
        return False

    window = LAST_ORDER[audience.last_order]
    if window is not None:
        dates = [p.at for p in profile.purchases if p.at is not None]
        if not dates:
            return False
        age = now - max(dates)
        after, up_to = window
        if after and age <= timedelta(days=after):
            return False
        if up_to is not None and age > timedelta(days=up_to):
            return False

    if audience.bought and not _mentions(
            audience.bought, (name for p in profile.purchases for name in p.products)):
        return False

    if audience.waiting == "yes":
        if not profile.waiting_for:
            return False
        if audience.waiting_for and not _mentions(audience.waiting_for,
                                                  profile.waiting_for):
            return False

    if audience.activity in ("new7", "new30"):
        days = 7 if audience.activity == "new7" else 30
        if profile.joined_at is None or now - profile.joined_at > timedelta(days=days):
            return False
    elif audience.activity == "idle30":
        if profile.last_seen is not None and now - profile.last_seen <= timedelta(days=30):
            return False

    return True


def _mentions(word: str, names) -> bool:
    """Case-insensitive in every alphabet, which SQLite's LIKE is not: «Сироватка»
    and «сироватка» are the same word to a person typing a filter."""
    needle = word.casefold()
    return any(needle in name.casefold() for name in names)
