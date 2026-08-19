"""Somewhere a message can be delivered to one person, and the ways it can fail.

Three outcomes, because the sender has to do three different things and only the
transport can tell them apart:

* it went — the queue row is done;
* it was refused for now — a rate limit that names its own wait, so the retry
  schedule comes from the answer rather than from a guess;
* it can never go — the person blocked the bot or the chat no longer exists, and
  retrying that for five attempts costs five requests to be told the same thing.

They are exceptions rather than a returned result because the middle and last
are exceptional for every caller: a scenario that forgets to check a status code
sends nothing and reports success, while one that forgets to catch gets a loud
failure it can see.

The port names no transport. `RateLimited` is Telegram's 429 today and would be
anyone else's tomorrow; nothing here knows which.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable


class RecipientGone(Exception):
    """The chat cannot receive this or anything else: blocked, deleted, gone."""


class RateLimited(Exception):
    """Refused for now, with the wait the transport asked for."""

    def __init__(self, retry_after: float) -> None:
        super().__init__(f"rate limited, retry after {retry_after}s")
        self.retry_after = retry_after


@runtime_checkable
class Notifier(Protocol):
    """One message, to one person, now."""

    async def send(self, chat_id: int, payload: dict, *, silent: bool) -> None:
        """Deliver `payload` to `chat_id`.

        `silent` is quiet hours already decided by the caller — the transport
        applies it, it does not judge it. Raises RateLimited or RecipientGone
        where the transport says so; any other exception means "unknown, try
        again later", which is what the sender assumes about anything it does
        not recognise.
        """
        ...
