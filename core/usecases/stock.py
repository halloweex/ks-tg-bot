"""Back-in-stock: poll availability, queue a message for whoever was waiting.

The first sender to move onto the outbox, which §6.6 asks for in this order and
for this reason: the subscription is an already-working proactive channel with a
table of subscribers, it is the smallest of the three, and it is the shape
`replenishment` will have — so whatever is wrong with the outbox is found here,
on a feature with a handful of subscribers, rather than on a broadcast.

**What changed in the move, and what deliberately did not.** The message is now
queued instead of sent, so a restart between the poll and the send no longer
loses it, a rate limit no longer costs anybody their notification, and a blocked
chat unsubscribes itself. The wording, the confetti, the one-message-per-person
grouping and the clearing of subscriptions are exactly as they were.

**Why the subscriptions are cleared even though nothing has been sent yet.** The
promise is "tell me when it is back", once. Keeping the row until delivery would
mean the next sweep queueing it again for anybody whose message is still in the
queue, and the dedup key that stops that is cheaper than the state machine that
would otherwise be needed. What the queue guarantees from here is that the
message survives a restart — which the direct send never did.

Reading availability is a port, so a test needs no network; the writing side is
still repositories imported directly, as everywhere else until the UnitOfWork
has an engine under it.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from loguru import logger

from core.domain.campaign import daily
from core.domain.stock import restocked
from core.i18n import customer_texts
from core.ports.catalog import StockLevels
from core.repos.outbox import enqueue
from core.repos.stock import (clear_subscriptions, get_stock_levels,
                              save_stock_levels, subscribers_for)
from core.repos.users import get_user_language
from core.texts import shorten_name

# Telegram's 🎉 effect. The one moment in this bot that is unambiguously good
# news for the person reading it — their product is back — and the only place an
# effect is not noise. Best effort: the sender drops it rather than the message
# if Telegram stops recognising the id.
CONFETTI_EFFECT_ID = "5046509860389126442"

# What the outbox calls these, and what §6.2's policy is read for. A restock
# notification would rather arrive twice than not at all: the customer asked to
# be told, and the second copy costs them a glance.
KIND = "stock"


@dataclass(frozen=True)
class Swept:
    """What one sweep found and queued."""

    skus_back: int = 0
    chats_queued: int = 0
    baseline: bool = False


async def check_once(catalogue: StockLevels, *, today: date | None = None) -> Swept:
    """One poll. Queues at most one message per person, whatever they subscribed to."""
    current = await catalogue.get_stock()
    if not current:
        # {} means the read failed, not that the shop is empty — comparing
        # against it would announce a restock of everything on the next poll.
        logger.warning("Back-in-stock: empty stock snapshot, skipping this round")
        return Swept()

    previous = await get_stock_levels()
    back = restocked(previous, current)
    await save_stock_levels(current)

    if not previous:
        logger.info("Back-in-stock: baseline recorded for {} skus", len(current))
        return Swept(baseline=True)
    if not back:
        return Swept()

    waiting = await subscribers_for(back)
    if not waiting:
        logger.info("Back-in-stock: {} sku(s) returned, nobody waiting", len(back))
        return Swept(skus_back=len(back))

    # One message per person, not per product: several favourites can return in
    # the same sweep and that should not read as a burst of notifications.
    by_chat: dict[int, list[tuple[str, str]]] = {}
    for chat_id, sku, name in waiting:
        by_chat.setdefault(chat_id, []).append((sku, name))

    campaign = daily(KIND, today)
    queued = 0
    fulfilled: list[tuple[int, str]] = []
    for chat_id, items in by_chat.items():
        skus = sorted(sku for sku, _name in items)
        message_id = await enqueue(
            chat_id, KIND, campaign,
            {"text": await _text(chat_id, [name for _sku, name in items]),
             "effect_id": CONFETTI_EFFECT_ID},
            # Survives the crash this move exists to survive: if the process
            # dies between queueing and clearing the subscriptions, the next
            # sweep finds them still there and would queue a second copy.
            dedup_key=f"{campaign}:{chat_id}:{'|'.join(skus)}",
        )
        fulfilled += [(chat_id, sku) for sku in skus]
        queued += 1 if message_id is not None else 0

    await clear_subscriptions(fulfilled)
    logger.info("Back-in-stock: {} sku(s) returned, queued for {} of {} waiting chats",
                len(back), queued, len(by_chat))
    return Swept(skus_back=len(back), chats_queued=queued)


async def _text(chat_id: int, names: list[str]) -> str:
    """The message, in the language this customer reads."""
    t = customer_texts(await get_user_language(chat_id))
    lines = [t.MSG_BACK_IN_STOCK_HEADER, ""]
    lines += [f"• {shorten_name(name, 60)}" for name in names]
    return "\n".join(lines)
