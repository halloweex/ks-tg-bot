"""What the loyalty programme has to say about one person.

The programme itself lives in Rivo, on the shop's side: it holds the points,
decides the tiers, runs the referrals and pays for them. Nothing here competes
with any of that. This is the shape an event has to be in before the bot can
tell somebody about it — which is the one thing the bot can do that the shop
cannot, since it may write first.

The names are ours, not Rivo's. Their `balance_transaction/created` becomes
POINTS here, and the adapter is where the two vocabularies meet, so a rename on
their side is one file rather than a search across handlers.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Kind(str, Enum):
    """What happened, in the only four flavours worth a message.

    Deliberately not one per Rivo event: they publish thirty, most of which are
    somebody else's business (billing attempts, membership rebills) or arrive
    in pairs that say the same thing to a customer.
    """

    POINTS = "points"
    TIER = "tier"
    EXPIRING = "expiring"
    REFERRAL = "referral"


@dataclass(frozen=True, slots=True)
class LoyaltyEvent:
    """One thing that happened, and who it happened to.

    `email` because that is how the loyalty programme knows people, and the
    only field of theirs this bot can join on — see core.ports.users.ChatsByEmail.

    `points` is the change and `balance` is where it left them: a message that
    says both ("+22, тепер 527") answers the question the first number always
    raises. Zero means the event does not carry that number, not that it was
    zero.
    """

    kind: Kind
    email: str
    points: int = 0
    balance: int = 0
    tier: str = ""

    @property
    def is_addressable(self) -> bool:
        """Whether there is anybody to look up. An event with no email cannot
        reach a person, and Rivo sends a few of those (shop-level notices)."""
        return bool(self.email.strip())
