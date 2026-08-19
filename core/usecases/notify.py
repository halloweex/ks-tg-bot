"""Take what is due out of the outbox and deliver it. One pass, no loop.

The loop belongs to whoever hosts this — today `bot/outbox.py`, tomorrow a
worker — and everything that decides *what happens to a message* lives here,
where it can be tested against a fake transport and no database of anybody's
patience.

What one pass does, in order:

1. **Park what nobody can resolve.** A row whose lock expired without being
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
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from loguru import logger

from core.domain.quiet import is_quiet_now
from core.ports.notifier import Notifier, RateLimited, RecipientGone
from core.repos.outbox import (MAX_ATTEMPTS, claim, mark_failed, mark_sent, park,
                               uncertain)
from core.repos.users import opt_out_user

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
    notifier: Notifier, *, limit: int = 50, now: datetime | None = None
) -> Delivered:
    """One pass over what is due. Returns what happened to it."""
    moment = now or datetime.now(timezone.utc)
    sent = retried = parked = unsubscribed = 0

    for row in await uncertain(now=moment):
        # Not a failure of this pass: it is a message from before a crash whose
        # type refuses to guess. Parked so that it is visible and finite.
        await park(row["id"], "left in doubt by a restart; policy is review")
        parked += 1
        logger.warning(
            "Outbox: {} #{} parked for review — a restart left it unresolved",
            row["type"], row["id"],
        )

    for row in await claim(limit, now=moment):
        message_id = row["id"]

        if row["attempts"] > MAX_ATTEMPTS:
            await park(message_id,
                       f"{row['attempts']} attempts, last: {row['last_error']}")
            parked += 1
            logger.error("Outbox: {} #{} parked after {} attempts, last error: {}",
                         row["type"], message_id, row["attempts"], row["last_error"])
            continue

        try:
            await notifier.send(
                row["chat_id"], _payload(row), silent=is_quiet_now(moment)
            )
        except RecipientGone as exc:
            # Both halves matter: the message can never arrive, and neither can
            # the next one. Unsubscribing is what stops a blocked chat costing a
            # send slot on every broadcast from now on (§6.1).
            await park(message_id, f"recipient gone: {exc}")
            await opt_out_user(row["chat_id"])
            parked += 1
            unsubscribed += 1
            logger.info("Outbox: chat {} is gone, parked and unsubscribed",
                        row["chat_id"])
        except RateLimited as exc:
            # The wait comes from the answer rather than from the backoff table:
            # guessing shorter gets another 429, guessing longer wastes the time
            # the transport just told us about.
            await mark_failed(message_id, f"rate limited {exc.retry_after}s",
                              retry_in=timedelta(seconds=exc.retry_after), now=moment)
            retried += 1
        except Exception as exc:  # noqa: BLE001 — unknown means "try again later"
            await mark_failed(message_id, f"{type(exc).__name__}: {exc}",
                              retry_in=backoff(row["attempts"]), now=moment)
            retried += 1
            logger.warning("Outbox: {} #{} failed ({}), attempt {} of {}",
                           row["type"], message_id, exc, row["attempts"], MAX_ATTEMPTS)
        else:
            await mark_sent(message_id)
            sent += 1

    result = Delivered(sent=sent, retried=retried, parked=parked,
                       unsubscribed=unsubscribed)
    if result.touched:
        logger.info("Outbox: sent {}, retried {}, parked {}",
                    result.sent, result.retried, result.parked)
    return result


def _payload(row: dict) -> dict:
    """The message as the transport needs it, from the row as stored.

    Tolerant on purpose: a row whose payload was pruned (§6.4) or written by an
    older version must not take the whole pass down with a decode error. It
    fails as one message the sender reports, which is the only failure mode with
    a person attached to it.
    """
    import json

    try:
        payload = json.loads(row.get("payload") or "{}")
    except (TypeError, ValueError):
        payload = {}
    payload.setdefault("campaign_key", row.get("campaign_key", ""))
    return payload
