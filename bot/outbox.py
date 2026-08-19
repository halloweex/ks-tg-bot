"""Telegram at the end of the outbox, and the loop that keeps emptying it.

Two things live here and nothing else: how a queued message becomes a Telegram
call, and how often the queue is looked at. Everything about *what* to do with
the result — retry, park, unsubscribe, stay quiet at night — is in
`core.usecases.notify`, where it can be tested without a token.

The translation is the interesting half. aiogram raises one exception type per
API failure and the sender needs three answers, so this file is where
`TelegramRetryAfter` becomes "wait exactly this long" and `TelegramForbiddenError`
becomes "this chat can never receive anything again". Anything not recognised
is left as it is and reaches the sender as "unknown, try later", which is the
right default for a transport that keeps inventing error messages.

It runs in the bot process, like the sync and the stock watcher, and for the
same reason: the capture in core/repos/outbox.py is safe while exactly one
process claims, and that is the constraint the deferral of stage 3 rests on
(§4.2). Moving it to a worker is a `__main__` and a compose service — and, on
that day, the rule stops being a comment and becomes `SKIP LOCKED`.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from aiogram import Bot
from aiogram.exceptions import (TelegramBadRequest, TelegramForbiddenError,
                                TelegramRetryAfter)
from loguru import logger

from core.ports.notifier import RateLimited, RecipientGone
from core.repos.outbox import prune, queue_depth
from core.usecases.notify import deliver_once

# How often the queue is looked at. Five seconds, because the first thing to
# arrive through it is a restock notification and the second is a parcel status,
# and neither is worth a minute of delay when the check is one indexed query
# against a table that is empty most of the time.
POLL_INTERVAL_SECONDS = 5

# How many messages one pass takes. §6.3 says fifty; at the pace below that is
# under three seconds of sending, so a pass never overlaps the next tick.
BATCH = 50

# Telegram tolerates roughly thirty messages a second overall. This is the same
# 50ms the stock watcher already paced itself with, kept in the transport where
# it belongs rather than in the scenario.
_PACE_SECONDS = 0.05

# A message can no longer be delivered to this chat, whatever it says. Matched
# on text because aiogram reports all of them as one exception type.
_GONE = ("chat not found", "user is deactivated", "bot was blocked",
         "peer_id_invalid", "chat_id is empty")

# The pruning of §6.4 is a once-a-day job and there is no nightly batch yet, so
# the loop that is already awake does it.
_PRUNE_EVERY = timedelta(days=1)


class TelegramNotifier:
    """core.ports.notifier.Notifier over aiogram."""

    def __init__(self, bot: Bot) -> None:
        self._bot = bot

    async def send(self, chat_id: int, payload: dict, *, silent: bool) -> None:
        text = payload.get("text") or ""
        if not text:
            # Nothing to send is not a transport failure, and retrying it five
            # times would only prove that. Parked by the sender as unknown.
            raise ValueError("queued message has no text")

        try:
            await self._send(chat_id, text, payload, silent=silent)
        except TelegramRetryAfter as exc:
            raise RateLimited(exc.retry_after) from exc
        except TelegramForbiddenError as exc:
            raise RecipientGone(str(exc)) from exc
        except TelegramBadRequest as exc:
            if any(marker in exc.message.lower() for marker in _GONE):
                raise RecipientGone(exc.message) from exc
            raise
        finally:
            # Paced whatever the outcome: a 400 costs the same rate budget as a
            # delivered message.
            await asyncio.sleep(_PACE_SECONDS)

    async def _send(self, chat_id: int, text: str, payload: dict, *, silent: bool) -> None:
        """One call, with the message effect dropped rather than obeyed.

        An effect id Telegram stops recognising must not cost anybody their
        notification — the same fallback the stock watcher has had since the
        confetti was added.
        """
        effect = payload.get("effect_id")
        try:
            await self._bot.send_message(
                chat_id, text,
                message_effect_id=effect or None,
                disable_notification=silent,
            )
        except TelegramBadRequest as exc:
            if not effect or "effect" not in exc.message.lower():
                raise
            logger.debug("Message effect rejected ({}), sending plain", exc.message)
            await self._bot.send_message(chat_id, text, disable_notification=silent)


async def watch(bot: Bot) -> None:
    """Empty the outbox, forever. One bad pass never kills the loop."""
    notifier = TelegramNotifier(bot)
    pruned_at = datetime.now(timezone.utc)
    logger.info("Outbox sender started ({}s interval)", POLL_INTERVAL_SECONDS)

    while True:
        try:
            await deliver_once(notifier, limit=BATCH)

            now = datetime.now(timezone.utc)
            if now - pruned_at >= _PRUNE_EVERY:
                pruned_at = now
                removed = await prune()
                depth = await queue_depth()
                logger.info(
                    "Outbox pruned: {} payload(s) cleared, {} row(s) dropped; "
                    "queue now due={} parked={} sent={}",
                    removed["payloads_cleared"], removed["rows_dropped"],
                    depth["due"], depth["parked"], depth["sent"],
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — the queue outlives its own bugs
            logger.exception("Outbox pass failed: {}", exc)
        await asyncio.sleep(POLL_INTERVAL_SECONDS)
