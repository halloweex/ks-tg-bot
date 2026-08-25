"""What scenarios ask about people, outside anybody's transaction.

`UserProfiles` lives in `core/ports/repositories.py` because it is on the
UnitOfWork: binding a number and writing the orders behind it must land
together or not at all. Everything here is the opposite case — a question or a
write that has no second half to be atomic with — so it is passed to a scenario
as an argument and answers on its own.

**The identity rule, stated once for the whole module.** Everything called
outside a transaction is keyed by `chat_id`; everything on the unit is keyed by
the surrogate `users.id`; the single crossing is
`UserProfiles.identify(chat_id)`. Two spellings of "who" is one more than
anybody wants, and the reason there are two is that Postgres mints an id the
caller cannot have guessed while SQLite's natural key is the chat itself. A
port that pretended otherwise would be a port that lies under one of the two
engines.

Split into small protocols rather than one object about people, because the
callers are small: a loop rendering one message per recipient wants a language
and nothing else, and a port it must construct a person to use is a port that
tempts the caller to fetch a whole profile and read one field off it.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class LanguageChoice(Protocol):
    """The language a customer picked for themselves, if they ever did."""

    async def chosen_by(self, chat_id: int) -> str | None:
        """The language this chat explicitly chose, or None if it never did.

        None is an answer, not a failure, and the distinction is the whole
        contract: it means fall back to the `language_code` Telegram supplies,
        which is what core.i18n does when handed None. An implementation
        returning the default instead would pin every customer who never opened
        settings to Ukrainian, and nothing anywhere would look wrong.
        """
        ...


@runtime_checkable
class MailingList(Protocol):
    """Who may be written to, and the one write that changes the answer.

    Both halves in one port because they are one rule seen from two ends: the
    sender learns a chat is gone and opts it out (§6.1), and the next broadcast
    must not address it. As two ports, the test that proves exactly that —
    deliver to a blocked chat, start a broadcast, see one fewer recipient —
    would need two fakes agreeing about a table neither of them owns, which is
    how they stop agreeing.

    Deliberately not on the unit. An opt-out belongs outside the transaction of
    the message that provoked it: "Telegram says this person blocked us" stays
    true whether that send is committed, retried or rolled back.
    """

    async def recipients(self) -> list[int]:
        """Every chat that has not opted out.

        The whole list, materialised, because the caller snapshots it: a
        broadcast's audience is fixed at the moment of sending, and an opt-out a
        minute later does not pull a queued message back out. Streaming it would
        make that snapshot a lie by degrees, with the size of the lie depending
        on how long the queue took to fill.
        """
        ...

    async def opt_out(self, chat_id: int) -> None:
        """Stop writing to this chat. Idempotent, because the caller is not.

        The sender discovers a gone recipient once per queued message, so the
        same person can be opted out several times in one pass. An
        implementation that raised on the second call would turn one blocked
        customer into a failed delivery pass.
        """
        ...
