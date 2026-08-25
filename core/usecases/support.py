"""A manager's answer on its way back to the customer.

The third and last sender of stage 6 (§6.7). Only one direction moves: the
answer going out. The customer's message going *to* the support chat stays a
direct send, and that is not an omission — the thread map is built from the ids
of the messages the bot puts in that chat (`support_threads`), and a queued send
has no id until it happens. Queueing that direction would mean a manager
replying to a message the bot has not registered yet.

What the move buys is the failure the section calls the worst one: a manager who
believes they answered while the customer got nothing. A direct send that failed
raised inside a handler and was visible to nobody; a queued one is retried, and
if it truly cannot be delivered it lands on the shelf with an alert.

**Quiet hours do not apply here**, and that is the reason the column exists. A
restock at 03:00 is the bot's idea and can arrive without a sound; a manager
answering at 23:00 is a person replying to a person who is waiting, and deciding
they can read it tomorrow is not the bot's call.

**The policy is `retry`.** A support answer that arrives twice is an
embarrassment; one that never arrives is the thing this whole subsystem was
rewritten to prevent (§6.7).
"""
from __future__ import annotations

from core.domain.campaign import daily
from core.domain.delivery import OnUncertain
from core.ports.outbox import MessageQueue

KIND = "support"


async def queue_reply(
    queue: MessageQueue,
    chat_id: int,
    *,
    text: str | None = None,
    copy_from: tuple[int, int] | None = None,
) -> int | None:
    """Queue one answer to one customer. Returns the queued id.

    `copy_from` is (chat_id, message_id) of the manager's own message, for the
    photos, voice notes and documents that cannot be retyped as text. Both may
    be given: the prefix line and then whatever was actually sent, in one row so
    they cannot arrive out of order or half.
    """
    # Shown as "typing…" for a moment before it lands. A manager's answer is a
    # person talking, and since it carries no label saying so any more, this is
    # what is left to say it — see bot/outbox.py, which owns the pause.
    payload: dict = {"typing": True}
    if text:
        payload["text"] = text
    if copy_from:
        payload["copy"] = {"from_chat_id": copy_from[0], "message_id": copy_from[1]}
    if not (text or copy_from):
        raise ValueError("a support reply with nothing in it")

    return await queue.queue(
        chat_id, KIND, daily(KIND), payload,
        on_uncertain=OnUncertain.RETRY,
        respect_quiet=False,
    )
