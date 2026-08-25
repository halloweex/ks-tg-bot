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
from core.ports.outbox import MessageQueue
from core.ports.profiles import BirthdaySource
from core.ports.users import KnownBirthdays, LanguageChoice

KIND = "bday"

# How many customers are asked about per run. The sweep runs hourly, so this is
# a couple of thousand people a day — far more than this shop signs up — and it
# keeps the API cost invisible next to the order sync.
ASK_PER_RUN = 50

# How long an answer stays good. A birthday is something people fill in long
# after they sign up, so it is re-asked rather than asked once. Here rather than
# in storage: how often this sweep costs an API call is the sweep's decision.
STALE_DAYS = 90


@dataclass(frozen=True)
class Swept:
    """What one run asked and queued."""

    asked: int = 0
    learned: int = 0
    greeted: int = 0


async def check_once(
    profiles: BirthdaySource,
    known: KnownBirthdays,
    languages: LanguageChoice,
    queue: MessageQueue,
    *,
    today: date | None = None,
    card_url: str = "",
) -> Swept:
    """One run: ask about a few people, then greet whoever is celebrating.

    Four ports, which is more than any other scenario here takes, and the count
    is the honest shape rather than a smell: this sweep asks Telegram, writes
    what it learned, reads a language per person and queues a message. Each is a
    different outside thing, and the alternative — one object about birthdays —
    would be an object that has to be built out of all four to test any one.
    """
    asked, learned = await _ask_a_few(profiles, known)
    greeted = await _greet_todays(known, languages, queue,
                                  today or date.today(), card_url)
    return Swept(asked=asked, learned=learned, greeted=greeted)


async def _ask_a_few(
    profiles: BirthdaySource, known: KnownBirthdays
) -> tuple[int, int]:
    """Fill in the column for the customers nobody has asked about lately."""
    chats = await known.to_ask(ASK_PER_RUN, stale_days=STALE_DAYS)
    asked = learned = 0
    for chat_id in chats:
        birthdate = await profiles.get_birthday(chat_id)
        if birthdate is None:
            # The ask itself failed. Left unstamped so it is retried, rather
            # than recorded as "this person has no birthday".
            continue
        await known.remember(chat_id, birthdate)
        asked += 1
        learned += 1 if birthdate else 0
    if asked:
        logger.info("Birthdays: asked about {} chat(s), {} had a date", asked, learned)
    return asked, learned


async def _greet_todays(
    known: KnownBirthdays,
    languages: LanguageChoice,
    queue: MessageQueue,
    today: date,
    card_url: str = "",
) -> int:
    """Queue one greeting per person celebrating today."""
    chats = await known.celebrating_on(today.strftime("%m-%d"))
    if not chats:
        return 0

    campaign = daily(KIND, today)
    greeted = 0
    for chat_id in chats:
        t = customer_texts(await languages.chosen_by(chat_id))
        payload = {"text": t.MSG_BIRTHDAY}
        if card_url:
            # The brand's own card: a burgundy field, the mark, the wordmark.
            # The only place in a chat where the palette is visible at all —
            # Telegram draws every word in the reader's own theme and font.
            payload["photo"] = card_url
        queued = await queue.queue(
            chat_id, KIND, campaign,
            {
                **payload,
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
