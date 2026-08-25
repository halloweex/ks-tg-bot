"""Telling somebody what the loyalty programme just did for them.

The programme is Rivo's and stays Rivo's: it awards the points, decides the
tiers, runs the referrals and pays for them. This scenario adds the one thing
the shop's own channels cannot — a message in Telegram a minute later, rather
than an email opened on Thursday, or never.

Which is also why so little happens here. Nothing is calculated, nothing is
promised, no number is invented: the event carries the figures and this queues
a sentence around them. If the two ever disagree, the loyalty programme is
right and the bot is wrong.
"""
from __future__ import annotations

from loguru import logger

from core.domain.campaign import daily
from core.domain.loyalty import Kind, LoyaltyEvent
from core.i18n import customer_texts
from core.ports.outbox import MessageQueue
from core.ports.users import ChatsByEmail, LanguageChoice

KIND = "loyalty"


def _text(event: LoyaltyEvent, t) -> str:
    """The sentence for this event, in the customer's language."""
    if event.kind is Kind.POINTS:
        return t.MSG_LOYALTY_POINTS.format(points=event.points, balance=event.balance)
    if event.kind is Kind.TIER:
        return t.MSG_LOYALTY_TIER.format(tier=event.tier)
    if event.kind is Kind.EXPIRING:
        return t.MSG_LOYALTY_EXPIRING.format(balance=event.balance)
    return t.MSG_LOYALTY_REFERRAL


async def announce(
    event: LoyaltyEvent,
    chats: ChatsByEmail,
    languages: LanguageChoice,
    queue: MessageQueue,
    *,
    account_url: str = "",
) -> bool:
    """Queue a message about this event, if there is anybody to send it to.

    False is the ordinary outcome, not a failure: the loyalty programme has
    thousands of customers and this bot has a handful, so most events are about
    somebody who has never opened it. Points spent rather than earned are
    skipped for a different reason — the customer just spent them, on purpose,
    and does not need telling.
    """
    if not event.is_addressable:
        return False
    if event.kind is Kind.POINTS and event.points <= 0:
        return False

    chat_id = await chats.chat_for(event.email)
    if chat_id is None:
        return False

    t = customer_texts(await languages.chosen_by(chat_id))
    payload: dict = {"text": _text(event, t)}
    if account_url:
        # Where the points actually live. The bot reports; the account is
        # where they are spent, and it belongs to the shop.
        payload["keyboard"] = {"inline_keyboard": [[{
            "text": t.BTN_LOYALTY_ACCOUNT, "url": account_url}]]}

    campaign = daily(KIND)
    queued = await queue.queue(
        chat_id, KIND, campaign, payload,
        # One per person per kind per day. Rivo can award points several times
        # in one order (a purchase, a review, a birthday), and three buzzes in
        # a minute is how a helpful channel becomes a muted one.
        dedup_key=f"{campaign}:{event.kind.value}:{chat_id}",
    )
    if queued is None:
        logger.debug("Loyalty: {} for chat {} was already queued today",
                     event.kind.value, chat_id)
        return False

    logger.info("Loyalty: queued {} for chat {}", event.kind.value, chat_id)
    return True
