"""Take what is due out of the outbox and deliver it. One pass, no loop.

The loop belongs to whoever hosts this — today `bot/outbox.py`, tomorrow a
worker — and everything that decides *what happens to a message* lives here,
where it can be tested against a fake transport and no database of anybody's
patience.

What one pass does, in order:

1. **Shelve what nobody can resolve.** A row whose lock expired without being
   released, on a type that would rather be missed than doubled (§6.2), is put
   on the shelf. It is the only outcome that is neither sent nor retried, and
   leaving it in the queue would mean a broadcast that resends itself after
   every restart.
2. **Claim a batch**, which locks it and counts the attempt (§6.3).
3. **Send each one outside any transaction**, because a `retry_after` is tens of
   seconds and a transaction held across it is how a queue becomes a pile of
   stuck locks.

Quiet hours are applied here rather than in the transport: the rule is the
shop's (§ core.domain.quiet), and the transport only does as it is told. Nothing
is deferred until morning — a restock at 03:00 is still true at 03:00, it simply
arrives without a sound.

The queue and the mailing list arrive as arguments. This scenario is the one
that decides what a failed send means, and it should be answerable for that
against a fake queue rather than against a file on disk.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from loguru import logger

from core.domain.delivery import QueuedMessage
from core.domain.quiet import is_quiet_now
from core.ports.notifier import Notifier, RateLimited, RecipientGone
from core.ports.outbox import PendingMessages
from core.ports.users import MailingList

# How many captures one message gets before it goes on the shelf. Five, because
# the failures worth retrying are transient by definition — a network blip, a
# rate limit — and anything surviving five attempts is a message that will never
# be sent, quietly costing a send slot every two minutes.
#
# The sender's policy, so it lives with the sender. It sat in the repository
# only because that is where `attempts` is incremented, and nothing there ever
# read it: capture does not filter on attempts, this loop decides.
MAX_ATTEMPTS = 5

# The first retry waits a minute, then two, four, eight — capped, because past
# half an hour the wait is no longer about the failure and a person should be
# looking at the shelf instead.
_BACKOFF_CAP = timedelta(minutes=30)


@dataclass(frozen=True)
class Delivered:
    """What one pass did. Every field is a different thing to worry about."""

    sent: int = 0
    retried: int = 0
    parked: int = 0
    unsubscribed: int = 0

    @property
    def touched(self) -> int:
        return self.sent + self.retried + self.parked


def backoff(attempts: int) -> timedelta:
    """A minute, doubling, capped at half an hour.

    The exponent is clamped before it is used, not after: `2 ** 98` minutes is
    not a long wait, it is an OverflowError inside timedelta — and the one
    caller that could reach it is a message stuck in a loop, which is exactly
    when the sender must not be the thing that breaks.
    """
    doublings = min(max(attempts - 1, 0), 16)
    return min(timedelta(minutes=2 ** doublings), _BACKOFF_CAP)


async def deliver_once(
    notifier: Notifier,
    pending: PendingMessages,
    mailing: MailingList,
    *,
    limit: int = 50,
    now: datetime | None = None,
) -> Delivered:
    """One pass over what is due. Returns what happened to it."""
    moment = now or datetime.now(timezone.utc)
    sent = retried = parked = unsubscribed = 0

    for message in await pending.left_in_doubt(now=moment):
        # Not a failure of this pass: it is a message from before a crash whose
        # type refuses to guess. Shelved so that it is visible and finite.
        await pending.shelve(
            message.id, "left in doubt by a restart; policy is review"
        )
        parked += 1
        logger.warning(
            "Outbox: {} #{} parked for review — a restart left it unresolved",
            message.kind, message.id,
        )

    for message in await pending.take_due(limit, now=moment):
        message_id = message.id

        if message.attempts > MAX_ATTEMPTS:
            await pending.shelve(
                message_id,
                f"{message.attempts} attempts, last: {message.last_error}",
            )
            parked += 1
            logger.error("Outbox: {} #{} parked after {} attempts, last error: {}",
                         message.kind, message_id, message.attempts,
                         message.last_error)
            continue

        try:
            # Quiet hours are the bot's manners about its own messages. A
            # manager answering a customer is not the bot's idea, and the row
            # says so.
            silent = message.respect_quiet and is_quiet_now(moment)
            await notifier.send(message.chat_id, _payload(message), silent=silent)
        except RecipientGone as exc:
            # Both halves matter: the message can never arrive, and neither can
            # the next one. Unsubscribing is what stops a blocked chat costing a
            # send slot on every broadcast from now on (§6.1).
            #
            # The fact is stated, not spelled: the queue composes whatever text
            # it needs to count this apart from a send that merely broke.
            await pending.shelve(message_id, str(exc), recipient_gone=True)
            await mailing.opt_out(message.chat_id)
            parked += 1
            unsubscribed += 1
            logger.info("Outbox: chat {} is gone, parked and unsubscribed",
                        message.chat_id)
        except RateLimited as exc:
            # The wait comes from the answer rather than from the backoff table:
            # guessing shorter gets another 429, guessing longer wastes the time
            # the transport just told us about.
            await pending.retry_later(
                message_id, f"rate limited {exc.retry_after}s",
                wait=timedelta(seconds=exc.retry_after), now=moment,
            )
            retried += 1
        except Exception as exc:  # noqa: BLE001 — unknown means "try again later"
            await pending.retry_later(
                message_id, f"{type(exc).__name__}: {exc}",
                wait=backoff(message.attempts), now=moment,
            )
            retried += 1
            logger.warning("Outbox: {} #{} failed ({}), attempt {} of {}",
                           message.kind, message_id, exc, message.attempts,
                           MAX_ATTEMPTS)
        else:
            await pending.mark_sent(message_id)
            sent += 1

    result = Delivered(sent=sent, retried=retried, parked=parked,
                       unsubscribed=unsubscribed)
    if result.touched:
        logger.info("Outbox: sent {}, retried {}, parked {}",
                    result.sent, result.retried, result.parked)
    return result


def _payload(message: QueuedMessage) -> dict:
    """The message as the transport needs it.

    A copy, not the stored mapping, so what is handed to Telegram cannot alter
    the record of what was queued.

    **The campaign key used to be added here and is not any more.** It was put
    in for a reader that never existed: `bot/outbox.py::TelegramNotifier.send`
    reads `typing`, `text`, `copy`, `effect_id`, `photo` and `keyboard`, and has
    never looked at it. §6.4 meant it to be stamped into the `callback_data` of
    the button on the message — but no proactive message carries a callback
    button, so there was nothing to stamp it into and nothing to read it back
    out of. The key is on the outbox row, where it is written once and now
    finally read: `core/usecases/analytics.py::campaign_report`.

    The tolerance that used to live here — a payload that will not decode
    becomes {} rather than taking the whole pass down — moved into the queue
    with the decoding itself. It is still true, and it is now true for anybody
    reading the queue rather than only for this function.
    """
    return dict(message.payload)
