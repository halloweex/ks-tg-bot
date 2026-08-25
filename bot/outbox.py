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
from aiogram.types import InlineKeyboardMarkup
from pydantic import ValidationError
from aiogram.exceptions import (TelegramBadRequest, TelegramForbiddenError,
                                TelegramRetryAfter)
from loguru import logger

from bot.alerts import tell_admins
from core.ports.notifier import RateLimited, RecipientGone
from core.repos.outbox import prune, queue_depth
from core.usecases.broadcast import report_finished_jobs
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

# §6.3 asks for an attempt limit, a shelf for the dead and an alert. The first
# two are in the queue; this is the third. Repeated at most hourly, because a
# shelf that fills up says the same thing five hundred times and an alert
# repeating faster than anybody can act on it is how alerts get muted.
_REALERT_AFTER = timedelta(hours=1)

# How long the "typing…" shows before a manager's answer arrives. Long enough
# for the client to draw it, short enough that nobody waits: support answers are
# rare next to broadcasts, so this costs the single sender nothing worth
# measuring.
_TYPING_SECONDS = 1.2


class TelegramNotifier:
    """core.ports.notifier.Notifier over aiogram."""

    def __init__(self, bot: Bot) -> None:
        self._bot = bot

    async def send(self, chat_id: int, payload: dict, *, silent: bool) -> None:
        if payload.get("typing"):
            await self._typing(chat_id)
        text = payload.get("text") or ""
        copy = payload.get("copy") or None
        if not text and not copy:
            # Nothing to send is not a transport failure, and retrying it five
            # times would only prove that. Parked by the sender as unknown.
            raise ValueError("queued message has neither text nor anything to copy")

        try:
            if text:
                await self._send(chat_id, text, payload, silent=silent)
            if copy:
                # copy_message rather than forward_message: a forward would show
                # the customer that their answer came out of a support chat, and
                # a copy carries the photo, the voice note and the caption
                # without saying where it was typed.
                await self._bot.copy_message(
                    chat_id=chat_id,
                    from_chat_id=copy["from_chat_id"],
                    message_id=copy["message_id"],
                    disable_notification=silent,
                )
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

    async def _typing(self, chat_id: int) -> None:
        """A moment of "typing…" ahead of an answer a person wrote.

        Only for those: a restock notice is the bot's own idea and pretending
        somebody typed it would be a small lie. The pause is what makes the
        indicator visible at all — without it the message arrives in the same
        breath and the client has nothing to draw.

        Failures are ignored. The send right after this one will raise the same
        thing properly, and a chat action is not worth an exception of its own.
        """
        try:
            await self._bot.send_chat_action(chat_id, "typing")
        except Exception as exc:  # noqa: BLE001 — decoration, never a failure
            logger.debug("Could not show typing to {}: {}", chat_id, exc)
            return
        await asyncio.sleep(_TYPING_SECONDS)

    async def _send(self, chat_id: int, text: str, payload: dict, *, silent: bool) -> None:
        """One call, with the message effect dropped rather than obeyed.

        An effect id Telegram stops recognising must not cost anybody their
        notification — the same fallback the stock watcher has had since the
        confetti was added.

        A photo, where the message asked for one, carries the text as its
        caption: one bubble rather than a picture with an orphan under it.
        """
        effect = payload.get("effect_id")
        keyboard = _keyboard(payload)
        photo = _photo(payload, text)
        send = self._bot.send_photo if photo else self._bot.send_message
        extra = ({"photo": photo, "caption": text} if photo
                 else {"text": text})
        try:
            await send(chat_id=chat_id, **extra,
                       message_effect_id=effect or None,
                       reply_markup=keyboard,
                       disable_notification=silent)
        except TelegramBadRequest as exc:
            if not effect or "effect" not in exc.message.lower():
                raise
            logger.debug("Message effect rejected ({}), sending plain", exc.message)
            await send(chat_id=chat_id, **extra, reply_markup=keyboard,
                       disable_notification=silent)


# What Telegram allows under a photo. Longer than any greeting, but a message
# that outgrew it would be silently cut in half, and half a message is worse
# than a message with no picture on it.
_CAPTION_LIMIT = 1024


def _photo(payload: dict, text: str) -> str | None:
    """The picture this message asked to carry, if it can carry one.

    A url rather than a file: the assets are published beside the Mini App
    page, and Telegram fetches them itself. A text too long to be a caption
    keeps its picture off rather than losing its ending.
    """
    photo = payload.get("photo")
    if not photo:
        return None
    if len(text) > _CAPTION_LIMIT:
        logger.warning("Queued message is too long for a caption ({} chars), "
                       "sending it without the picture", len(text))
        return None
    return str(photo)


def _keyboard(payload: dict) -> InlineKeyboardMarkup | None:
    """The buttons a queued message asked to carry, if any.

    A dict rather than an object in the payload because the payload is JSON in
    a table: whatever queues the message is a scenario in `core`, which knows
    Telegram's shapes (it already writes "effect_id" and "copy") but not
    aiogram's classes. Rebuilt here, at the transport, where aiogram lives.

    A malformed keyboard costs the buttons rather than the message: this runs
    inside the sender, and a birthday greeting without its button is still a
    birthday greeting.
    """
    raw = payload.get("keyboard")
    if not raw:
        return None
    try:
        return InlineKeyboardMarkup.model_validate(raw)
    except ValidationError as exc:
        logger.warning("Queued message has an unusable keyboard: {}", exc)
        return None


async def watch(bot: Bot, admin_ids: list[int] | None = None) -> None:
    """Empty the outbox, forever. One bad pass never kills the loop."""
    notifier = TelegramNotifier(bot)
    pruned_at = datetime.now(timezone.utc)
    alerted_at: datetime | None = None
    logger.info("Outbox sender started ({}s interval)", POLL_INTERVAL_SECONDS)

    while True:
        try:
            result = await deliver_once(notifier, limit=BATCH)
            # Whoever started a broadcast is waiting for its summary, and this
            # loop is the thing already awake when the last message of it goes.
            await report_finished_jobs()

            now = datetime.now(timezone.utc)
            if result.parked and admin_ids and (
                alerted_at is None or now - alerted_at >= _REALERT_AFTER
            ):
                alerted_at = now
                depth = await queue_depth()
                await tell_admins(bot, admin_ids, _shelf_alert(result, depth))

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


def _shelf_alert(result, depth: dict[str, int]) -> str:
    """What an admin needs to decide whether to look now or after lunch."""
    lines = [
        f"📮 Outbox: {result.parked} message(s) went on the shelf.",
        f"Shelf now: {depth['parked']}. Due in the queue: {depth['due']}.",
    ]
    if result.unsubscribed:
        # Not a problem to fix: somebody blocked the bot, and that is the queue
        # working. Said out loud so the number in the line above is explained.
        lines.append(f"{result.unsubscribed} of them blocked the bot and were "
                     f"unsubscribed.")
    lines.append("SELECT * FROM outbox WHERE failed_at IS NOT NULL ORDER BY id DESC;")
    return "\n".join(lines)
