"""Somewhere a customer's own Telegram profile can be read.

One method, and it exists so the birthday sweep can be tested without a bot:
the scenario asks this, an adapter answers it with `getChat`, and a test answers
it with a dict.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class BirthdaySource(Protocol):
    """What Telegram knows about when somebody was born."""

    async def get_birthday(self, chat_id: int) -> str | None:
        """"MM-DD", "" when there is none to see, or None if the ask failed.

        The three answers are different on purpose. "" is an answer — this
        person has no visible date of birth — and is worth storing, because it
        is what stops the sweep asking about them again tomorrow. None means we
        learned nothing and should try again later.
        """
        ...
