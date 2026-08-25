"""Paying for a recommendation, once, after it has actually brought somebody.

The rule the owner chose: a friend counts when she has placed her **first
order**, not when she opened the bot. Opening a bot costs nothing, and a
programme that pays for that pays for nothing.

Two messages come out of one earned referral, and they go to different people
for different reasons. The customer is told her friend has ordered — that is
the promise being kept. The manager is told at the same time, because until a
Shopify token with rights to discounts exists, the promo code is written by a
person; when it exists, `code` in the referrals table is where it lands and this
module is the only thing that changes.

Idempotency is a row, not a guess: the sweep runs every quarter of an hour and
a reward is owed once.
"""
from __future__ import annotations

from dataclasses import dataclass

from loguru import logger

from core.domain.campaign import daily
from core.i18n import customer_texts
from core.repos.outbox import enqueue
from core.repos.referrals import (earned_referrals, record_reward,
                                  set_reward_code)
from core.repos.users import get_user_language

KIND = "referral"

# How many are paid for per run. The sweep is cheap — one query — and this is
# the cap on how many messages one round can queue.
PER_RUN = 50


@dataclass(frozen=True)
class Swept:
    """What one run found and owed."""

    earned: int = 0


async def check_once(prefix: str, *, code: str = "", reward: str = "",
                     discount_link: str = "") -> Swept:
    """Find the referrals that have come good, and pay for them once.

    `code` is what the shop created in its admin; with one, the customer gets
    it in the message and a button that applies it, and the reward is finished
    the moment she is told. Without one the message says a manager will write —
    which is what then happens — because a bot promising a code it cannot send
    is a promise that quietly stops being kept.
    """
    pairs = await earned_referrals(prefix, PER_RUN)
    if not pairs:
        return Swept()

    campaign = daily(KIND)
    earned = 0
    for friend_chat_id, referrer_chat_id in pairs:
        # The row first: if this process dies before the message is queued, the
        # customer is told nothing and nobody is paid twice. The other order
        # round would pay twice, and that is the worse failure.
        if not await record_reward(friend_chat_id, referrer_chat_id):
            continue

        t = customer_texts(await get_user_language(referrer_chat_id))
        payload: dict = {"text": t.MSG_REFERRAL_EARNED + (
            t.MSG_REFERRAL_CODE.format(code=code, reward=reward) if code
            else t.MSG_REFERRAL_BY_HAND)}
        if code and discount_link:
            # One tap: the link puts the code in her session and lands her in
            # the shop, the same mechanism the first-order offer uses.
            payload["keyboard"] = {"inline_keyboard": [[{
                "text": t.BTN_REFERRAL_USE, "url": discount_link,
                "style": "success"}]]}
        await enqueue(
            referrer_chat_id, KIND, campaign, payload,
            dedup_key=f"{campaign}:{friend_chat_id}",
        )
        # What was paid, recorded with the payment: a code handed out is the
        # thing anybody auditing this will ask about.
        if code:
            await set_reward_code(friend_chat_id, code)
        earned += 1

    logger.info("Referrals: {} of {} earned referral(s) paid for",
                earned, len(pairs))
    return Swept(earned=earned)
