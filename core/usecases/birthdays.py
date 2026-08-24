"""Greeting a customer on their birthday, and finding out when that is.

Two jobs on one schedule, and they are separate on purpose.

**Asking.** Telegram carries a date of birth on `getChat` when the person set
one and their privacy lets a bot read it. Most people have not, so the answer is
usually "there is none" — which is still an answer and is stored as one, because
what it buys is not asking that person again tomorrow. A slice is asked about
each run rather than everybody at once: the whole point is that this costs one
API call per customer per quarter.

**Greeting.** Reads the column, not the API. One message per person per day,
guaranteed by the dedup key, so a sweep that runs every hour cannot wish anybody
a happy birthday twelve times.

Nothing here promises a discount. There is no discount policy yet, and a bot
inventing one commits the business to it — the same reason the discount button
asks a manager instead of issuing a code. The greeting is the whole gift: a shop
that remembers is already unusual.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from loguru import logger

from core.domain.campaign import daily
from core.i18n import customer_texts
from core.ports.profiles import BirthdaySource
from core.repos.outbox import enqueue
from core.repos.users import (chats_with_birthday_on, chats_without_birthday,
                              get_user_language, save_birthday)

KIND = "bday"

# How many customers are asked about per run. The sweep runs hourly, so this is
# a couple of thousand people a day — far more than this shop signs up — and it
# keeps the API cost invisible next to the order sync.
ASK_PER_RUN = 50


@dataclass(frozen=True)
class Swept:
    """What one run asked and queued."""

    asked: int = 0
    learned: int = 0
    greeted: int = 0


async def check_once(profiles: BirthdaySource, *, today: date | None = None) -> Swept:
    """One run: ask about a few people, then greet whoever is celebrating."""
    asked, learned = await _ask_a_few(profiles)
    greeted = await _greet_todays(today or date.today())
    return Swept(asked=asked, learned=learned, greeted=greeted)


async def _ask_a_few(profiles: BirthdaySource) -> tuple[int, int]:
    """Fill in the column for the customers nobody has asked about lately."""
    chats = await chats_without_birthday(ASK_PER_RUN)
    asked = learned = 0
    for chat_id in chats:
        birthdate = await profiles.get_birthday(chat_id)
        if birthdate is None:
            # The ask itself failed. Left unstamped so it is retried, rather
            # than recorded as "this person has no birthday".
            continue
        await save_birthday(chat_id, birthdate)
        asked += 1
        learned += 1 if birthdate else 0
    if asked:
        logger.info("Birthdays: asked about {} chat(s), {} had a date", asked, learned)
    return asked, learned


async def _greet_todays(today: date) -> int:
    """Queue one greeting per person celebrating today."""
    chats = await chats_with_birthday_on(today.strftime("%m-%d"))
    if not chats:
        return 0

    campaign = daily(KIND, today)
    greeted = 0
    for chat_id in chats:
        t = customer_texts(await get_user_language(chat_id))
        queued = await enqueue(
            chat_id, KIND, campaign,
            {
                "text": t.MSG_BIRTHDAY,
                # The one thing a greeting can usefully offer: their own list,
                # with the photos. An inline button, because a queued message
                # can carry a keyboard but cannot carry a conversation.
                "keyboard": {"inline_keyboard": [[{
                    "text": t.BTN_BIRTHDAY_FAVOURITES,
                    "switch_inline_query_current_chat": "",
                }]]},
            },
            # One per person per day whatever happens: the sweep runs hourly,
            # and a birthday is not a thing to be wished twelve times.
            dedup_key=f"{campaign}:{chat_id}",
        )
        greeted += 1 if queued is not None else 0

    logger.info("Birthdays: {} of {} celebrating chat(s) queued", greeted, len(chats))
    return greeted
