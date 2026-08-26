"""When a phone number may be turned into "these orders are yours", and when not.

§4.8 of docs/architecture.md is the most dangerous section of that document, and
this module is the rule it ends on. The mechanism it guards — ask the CRM for a
number's orders and attach them to whoever just verified that number — hands a
customer somebody else's purchase history when it is wrong, and it is wrong
whenever one number is held by more than one person.

**This is measured, not hypothetical.** On 2026-08-03, against the production
CRM: 203 numbers belong to more than one buyer card, and 180 of those pairs have
orders on both sides. The section was originally written about Ukrainian
operators reissuing numbers — a risk in the future — and the measurement changed
it into a rule about the data that is already there. 203 of 18 884 numbers is
half a percent of the base, and that is the price of nobody ever seeing another
person's orders.

**Why the count of buyer cards is the whole test.** The CRM's by-number search
matches every card carrying that number, so the answer to one request is exactly
the set of people the CRM thinks the number belongs to. One card is one person.
Two is the case this module refuses, and no amount of window narrowing helps:
§4.8 says such numbers are not linked automatically *ever*, inside the twelve
month window or outside it. They are reachable only through ownership
confirmation — showing what was found and asking for a detail only the owner
knows — which does not exist yet and is not something a sweep can do.
"""
from __future__ import annotations

from collections.abc import Iterable

from core.domain.order import Order


def buyer_cards(orders: Iterable[Order]) -> set[str]:
    """The distinct CRM buyer cards behind these orders.

    Blank ids are dropped rather than counted as a card of their own: an order
    the CRM returned without a buyer says nothing about whose the number is, and
    counting it would turn one honest customer into two and refuse to link them.
    """
    return {str(order.buyer_id) for order in orders if str(order.buyer_id)}


def shared_by_several_people(orders: Iterable[Order]) -> bool:
    """True when this number's orders came back under more than one buyer card.

    The §4.8 refusal, in one question. Answered from the orders a by-number
    request returned, because that request is the only thing that can answer it:
    the bot cannot know whose a number is, and the CRM's own search tells it
    every time somebody asks.

    Zero cards is False. That is a customer whose orders carry no buyer at all,
    which is a CRM data problem rather than an identity one, and refusing to
    link them would deny a real customer their own history for somebody else's
    missing field.
    """
    return len(buyer_cards(orders)) > 1
