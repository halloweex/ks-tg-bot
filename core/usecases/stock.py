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

**Everything outside this function is a port now**, and the two storage ones are
deliberately two. `StockSnapshot` is the poller's own memory, replaced wholesale
on every round and worth nothing if lost; `RestockWatchlist` holds what
customers asked the bot for, and the sweep may only ever release an entry there,
never create one. As one port they would be one object with four methods and no
way to say that the asymmetry is a rule rather than the current shape of the
code — up to here it was held up by whoever remembered which functions of
`core/repos/stock.py` a sweep is allowed to call.

What the move does not change is atomicity. Queueing and clearing are still two
writes with a crash between them, papered over by the dedup key exactly as
before — the port series closes no such hole and must not look as though it
does.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from loguru import logger

from core.domain.campaign import daily
from core.domain.stock import restocked
from core.effects import CONFETTI
from core.i18n import customer_texts
from core.ports.catalog import StockLevels
from core.ports.outbox import MessageQueue
from core.ports.repositories import RestockWatchlist, StockSnapshot
from core.ports.users import LanguageChoice
from core.texts import shorten_name

# Telegram's 🎉 effect. The one moment in this bot that is unambiguously good
# news for the person reading it — their product is back — and the only place an
# effect is not noise. Best effort: the sender drops it rather than the message
# if Telegram stops recognising the id.
# Kept as a name here because this is where the notification is built, but the
# id itself lives in core/effects.py now — a second sender needed it, and an id
# spelled twice is an id that gets edited once.
CONFETTI_EFFECT_ID = CONFETTI

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


async def check_once(
    catalogue: StockLevels,
    snapshot: StockSnapshot,
    watchlist: RestockWatchlist,
    languages: LanguageChoice,
    queue: MessageQueue,
    *,
    today: date | None = None,
) -> Swept:
    """One poll. Queues at most one message per person, whatever they subscribed to.

    Five ports, and the first two are one word apart on purpose: `catalogue` is
    what the shop says now, `snapshot` is what it said last time. A restock
    exists only in the difference between them, which is why comparing them is
    domain code and neither port does any of it.
    """
    current = await catalogue.get_stock()
    if not current:
        # {} means the read failed, not that the shop is empty — comparing
        # against it would announce a restock of everything on the next poll.
        logger.warning("Back-in-stock: empty stock snapshot, skipping this round")
        return Swept()

    previous = await snapshot.last_seen()
    back = restocked(previous, current)
    await snapshot.remember(current)

    if not previous:
        logger.info("Back-in-stock: baseline recorded for {} skus", len(current))
        return Swept(baseline=True)
    if not back:
        return Swept()

    waiting = await watchlist.waiting_for(back)
    if not waiting:
        logger.info("Back-in-stock: {} sku(s) returned, nobody waiting", len(back))
        return Swept(skus_back=len(back))

    # One message per person, not per product: several favourites can return in
    # the same sweep and that should not read as a burst of notifications. The
    # port answers flat, one row per person per sku, precisely so that this
    # decision stays here rather than being made by whoever wrote the query.
    by_chat: dict[int, list[tuple[str, str]]] = {}
    for entry in waiting:
        by_chat.setdefault(entry.chat_id, []).append((entry.sku, entry.name))

    campaign = daily(KIND, today)
    queued = 0
    fulfilled: list[tuple[int, str]] = []
    for chat_id, items in by_chat.items():
        skus = sorted(sku for sku, _name in items)
        message_id = await queue.queue(
            chat_id, KIND, campaign,
            {"text": await _text(languages, chat_id,
                                 [name for _sku, name in items]),
             "effect_id": CONFETTI_EFFECT_ID},
            # Survives the crash this move exists to survive: if the process
            # dies between queueing and clearing the subscriptions, the next
            # sweep finds them still there and would queue a second copy.
            dedup_key=f"{campaign}:{chat_id}:{'|'.join(skus)}",
        )
        fulfilled += [(chat_id, sku) for sku in skus]
        queued += 1 if message_id is not None else 0

    await watchlist.release(fulfilled)
    logger.info("Back-in-stock: {} sku(s) returned, queued for {} of {} waiting chats",
                len(back), queued, len(by_chat))
    return Swept(skus_back=len(back), chats_queued=queued)


async def _text(languages: LanguageChoice, chat_id: int, names: list[str]) -> str:
    """The message, in the language this customer reads."""
    t = customer_texts(await languages.chosen_by(chat_id))
    lines = [t.MSG_BACK_IN_STOCK_HEADER, ""]
    lines += [f"• {shorten_name(name, 60)}" for name in names]
    return "\n".join(lines)
