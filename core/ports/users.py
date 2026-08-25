"""What scenarios ask about people, outside anybody's transaction.

`UserProfiles` lives in `core/ports/repositories.py` because it is on the
UnitOfWork: binding a number and writing the orders behind it must land
together or not at all. Everything here is the opposite case — a question or a
write that has no second half to be atomic with — so it is passed to a scenario
as an argument and answers on its own.

**The identity rule, stated once for the whole module.** Everything called
outside a transaction is keyed by `chat_id`; everything on the unit is keyed by
the surrogate `users.id`. Two spellings of "who" is one more than anybody wants,
and the reason there are two is that Postgres mints an id the caller cannot have
guessed while SQLite's natural key is the chat itself. A port that pretended
otherwise would be a port that lies under one of the two engines.

**The single crossing is `UserProfiles.bind_phone`**, which takes a `chat_id`
and hands the surrogate back. There is deliberately no `identify(chat_id)`: the
only operation that *can* produce an id is the one that can create the person,
because `users.phone_normalized` is NOT NULL and UNIQUE with no default, so no
row exists before a number is verified.

Which leaves a gap this module should name rather than imply. A scenario holding
an already-registered chat has nowhere to ask for its surrogate, so
`core/usecases/sync_orders.py` opens its unit with `user_id=chat_id` — true
under SQLite, where the two are the same number, and the one place the spellings
are knowingly confused. It is one of the four fixes docs/move-status.md defers
until after this series, and the day it is fixed is the day this paragraph turns
into a second method here.

Split into small protocols rather than one object about people, because the
callers are small: a loop rendering one message per recipient wants a language
and nothing else, and a port it must construct a person to use is a port that
tempts the caller to fetch a whole profile and read one field off it.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class ChatsByEmail(Protocol):
    """Which chat a loyalty programme's customer is, if any.

    The loyalty programme knows people by email; this bot knows them by chat,
    and the email it holds came from the CRM card at registration. So the join
    is one lookup, and it fails for most people: somebody who never registered
    here, or whose CRM card carried no email, simply has no chat to write to.
    None is that answer, and it is not an error.
    """

    async def chat_for(self, email: str) -> int | None:
        """The chat that email belongs to, or None."""
        ...


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


@runtime_checkable
class KnownBirthdays(Protocol):
    """The dates the bot has already learned, and who it has yet to ask.

    The storage counterpart of `core.ports.profiles.BirthdaySource`, which is
    Telegram. Two ports rather than one because they fail differently and are
    faked differently: the source can be unavailable, and its "I could not ask"
    must never be recorded as "there is none", while storage answering at all is
    the thing that stops the asking.
    """

    async def to_ask(self, limit: int, *, stale_days: int) -> list[int]:
        """Customers to ask Telegram about, never-asked ones first.

        Both numbers come from the caller because both are the scenario's
        policy: `limit` is a rate-limit decision, `stale_days` is how long an
        answer stays good — a birthday is something people fill in long after
        they sign up, so it is re-asked rather than asked once. Left as
        implementation defaults they would be two constants free to disagree
        between engines, and the symptom would be a sweep that quietly costs
        more API calls under one of them.

        The ordering is part of the contract: without "never asked first", a
        long tail of re-asks starves everybody who registered yesterday.
        """
        ...

    async def remember(self, chat_id: int, birthdate: str) -> None:
        """Store "MM-DD", or "" for somebody Telegram shows no date for.

        The empty string is written on purpose and is the only reason this sweep
        is cheap: it records that the question was asked, and most people have
        no visible date. Recording *when* it was asked is the implementation's
        business and deliberately not a second method — a scenario able to write
        a date without recording the ask is a scenario that will one day write
        one and put everybody back in the queue forever.
        """
        ...

    async def celebrating_on(self, month_day: str) -> list[int]:
        """Everyone whose birthday is "MM-DD", opt-outs already excluded.

        The exclusion is here rather than in the caller for the same reason
        MailingList owns it: "may this person be written to" is one question,
        and a greeting is still a message the bot decided to send. A caller that
        filters afterwards is a caller that can forget to, and the way you find
        out is somebody who unsubscribed getting a birthday card.
        """
        ...
