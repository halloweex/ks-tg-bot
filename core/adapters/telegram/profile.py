"""A customer's Telegram profile, as much of it as a bot may see.

Only `birthdate` so far. Telegram returns it from getChat for a private chat
when the person has set a date of birth *and* their privacy allows a bot to
read it — which most people have not done, so an empty answer is the normal
one and not a failure.
"""
from __future__ import annotations

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from loguru import logger


class TelegramProfiles:
    """Implements the BirthdaySource port over aiogram."""

    def __init__(self, bot: Bot) -> None:
        self._bot = bot

    async def get_birthday(self, chat_id: int) -> str | None:
        """"MM-DD", "" when there is none to see, None when the ask failed.

        A failure here is ordinary — a customer who blocked the bot, a chat
        Telegram will not resolve — and must not stop the sweep, so it is logged
        at debug and reported as "ask again later".
        """
        try:
            chat = await self._bot.get_chat(chat_id)
        except TelegramAPIError as exc:
            logger.debug("Could not read the profile of {}: {}", chat_id, exc)
            return None

        birthdate = getattr(chat, "birthdate", None)
        if birthdate is None:
            return ""
        return f"{birthdate.month:02d}-{birthdate.day:02d}"
