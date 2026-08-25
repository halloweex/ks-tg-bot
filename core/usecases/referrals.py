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

**Three ports since the move, and the ledger is deliberately one of them rather
than two.** Finding what has come good and writing that it has been paid are the
same table answering the same question from both ends — the row this sweep
writes is precisely what stops the next `earned()` returning that referral
again. Split across two ports, the test that proves exactly that (pay once, run
again, get nothing) would need two fakes agreeing about a table neither of them
owns, which is how they stop agreeing.

What is still not atomic is the pair of writes inside the loop: the reward row
goes down, then the message is queued. That order is the choice — the other one
pays twice when the process dies in between — and the port series does not close
it.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from loguru import logger

from core.domain.campaign import daily
from core.i18n import customer_texts
from core.ports.discounts import DiscountCodes
from core.ports.outbox import MessageQueue
from core.ports.repositories import ReferralLedger
from core.ports.users import LanguageChoice

KIND = "referral"

# How many are paid for per run. The sweep is cheap — one query — and this is
# the cap on how many messages one round can queue.
PER_RUN = 50


@dataclass(frozen=True)
class Swept:
    """What one run found and owed."""

    earned: int = 0


async def check_once(
    prefix: str,
    ledger: ReferralLedger,
    languages: LanguageChoice,
    queue: MessageQueue,
    *,
    code: str = "",
    reward: str = "",
    discount_link: str = "",
    discounts: DiscountCodes | None = None,
    percent: int = 0,
    link_for: Callable[[str], str] | None = None,
) -> Swept:
    """Find the referrals that have come good, and pay for them once.

    `prefix` stays the first argument rather than joining the ports behind it:
    it is not an outside thing being handed in, it is what the sweep is looking
    for. The deep link belongs to the bot, so the entry point says which one.

    `code` is what the shop created in its admin; with one, the customer gets
    it in the message and a button that applies it, and the reward is finished
    the moment she is told. Without one the message says a manager will write —
    which is what then happens — because a bot promising a code it cannot send
    is a promise that quietly stops being kept.

    `discounts` replaces that shared code with one of her own, created as the
    reward is paid: single-use, so it cannot be forwarded into a discount the
    shop never agreed to give. Failing to create one is not a failed reward —
    it falls back to `code`, and then to the manager, in that order. `link_for`
    builds the link that applies whichever code came out, and belongs to the
    caller because a url with campaign tags on it is not a scenario's business.
    """
    pairs = await ledger.earned(prefix, PER_RUN)
    if not pairs:
        return Swept()

    campaign = daily(KIND)
    earned = 0
    for pair in pairs:
        friend_chat_id = pair.friend_chat_id
        referrer_chat_id = pair.referrer_chat_id
        # The row first: if this process dies before the message is queued, the
        # customer is told nothing and nobody is paid twice. The other order
        # round would pay twice, and that is the worse failure.
        if not await ledger.record_reward(friend_chat_id, referrer_chat_id):
            continue

        # Hers, if the shop can make one; otherwise whatever was configured,
        # otherwise a person. Asked for here rather than once per run: a code
        # created and not handed out is a discount loose in the shop.
        hers = code
        link = discount_link
        if discounts is not None and percent > 0:
            issued = await discounts.issue_percentage(
                percent, title=f"Referral reward, chat {referrer_chat_id}")
            if issued:
                hers = issued
                link = link_for(issued) if link_for else ""

        t = customer_texts(await languages.chosen_by(referrer_chat_id))
        payload: dict = {"text": t.MSG_REFERRAL_EARNED + (
            t.MSG_REFERRAL_CODE.format(code=hers, reward=reward) if hers
            else t.MSG_REFERRAL_BY_HAND)}
        if hers and link:
            # One tap: the link puts the code in her session and lands her in
            # the shop, the same mechanism the first-order offer uses.
            payload["keyboard"] = {"inline_keyboard": [[{
                "text": t.BTN_REFERRAL_USE, "url": link,
                "style": "success"}]]}
        await queue.queue(
            referrer_chat_id, KIND, campaign, payload,
            dedup_key=f"{campaign}:{friend_chat_id}",
        )
        # What was paid, recorded with the payment: a code handed out is the
        # thing anybody auditing this will ask about.
        if hers:
            await ledger.record_code(friend_chat_id, hers)
        earned += 1

    logger.info("Referrals: {} of {} earned referral(s) paid for",
                earned, len(pairs))
    return Swept(earned=earned)
