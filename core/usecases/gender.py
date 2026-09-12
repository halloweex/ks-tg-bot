"""Working out which form of Ukrainian each customer is addressed in.

The decision itself is not made here and not in this repository: `buyer_gender`
in the warehouse holds one row per CRM buyer, with the method, the confidence and
the human overrides that produced it. What this scenario does is the join nobody
else can do — the warehouse knows buyers, the bot knows chats — and it writes the
answer where the middleware can read it for the price of a column it already
selects.

**Why a sweep and not a lookup on the customer's tap.** Three things the tap
cannot afford: a second database on the path of every update, a failure mode that
reaches a screen, and a forty-times-a-day question whose answer changes when
somebody corrects a name. A sweep has none of them — it runs beside the others,
an hour apart, and its worst day is a log line and yesterday's forms.

**A chat with no cards is answered, not skipped.** Registered, never ordered, or
ordered under a number that is not the one in Telegram: the warehouse has nothing
to say about them, and that is exactly the unmarked form's case. Skipping them
would leave them reading the feminine copy, which is the guess this whole
scenario exists to stop making.

Chats whose number belongs to several buyer cards (§4.8) are the one group left
out. They are absent from `phones()` on purpose — the bot refuses to link them at
all — and a grammatical form is the least of what they do not get from it.
"""
from __future__ import annotations

from loguru import logger

from core.domain.gender import Gender, resolve
from core.ports.gender import BuyerGenders
from core.ports.repositories import CustomerDirectory
from core.ports.users import GenderForm


async def refresh_forms(
    directory: CustomerDirectory,
    source: BuyerGenders,
    forms: GenderForm,
    *,
    only: set[int] | None = None,
) -> dict[int, str]:
    """Ask the warehouse about everybody, store what changed, return it.

    `only` narrows the sweep to a few chats — one, at registration, where the
    point is that a customer is addressed correctly on their first screen rather
    than within the hour. The map is read whole either way: it is one narrow row
    per buyer card, the same read the order sweep does every two minutes, and a
    per-chat query would be a second way to ask the same question.

    Writes only where the answer changed, so a sweep over a base that has not
    moved costs reads and nothing else — and `users.updated_at` goes on meaning
    "this person's profile changed" rather than "a sweep passed".

    Raises whatever the source raises. The caller's answer to the other database
    being down is to change nothing, and that is what happens if this stops
    halfway: every form already written stays, and the next round finishes the
    job.
    """
    cards: dict[int, set[str]] = {}
    for chat_id, buyer_id in await directory.buyers():
        if only is None or chat_id in only:
            cards.setdefault(chat_id, set()).add(str(buyer_id))
    for chat_id, _phone in await directory.phones():
        if only is None or chat_id in only:
            cards.setdefault(chat_id, set())

    if not cards:
        return {}

    asked: set[str] = set().union(*cards.values()) if cards else set()
    answers = await source.for_buyers(asked) if asked else {}

    changed: dict[int, str] = {}
    for chat_id, mine in cards.items():
        # A card the table has never heard of and a card whose gender is NULL
        # are both "no answer" here, which is what `resolve` ignores. The
        # difference between them matters to whoever maintains the classifier,
        # not to the sentence a customer reads.
        decided = resolve(answers.get(card) for card in mine)
        if await forms.form_for(chat_id) == decided.value:
            continue
        await forms.remember(chat_id, decided.value)
        changed[chat_id] = decided.value

    if changed:
        # Counts and not chat ids: this is an operational line, and which
        # customer is which gender is not something the log needs to carry.
        logger.info(
            "Gender forms updated: {} chat(s) — {} feminine, {} masculine, "
            "{} unmarked", len(changed),
            sum(1 for v in changed.values() if v == Gender.F.value),
            sum(1 for v in changed.values() if v == Gender.M.value),
            sum(1 for v in changed.values() if v == Gender.UNKNOWN.value),
        )
    return changed
