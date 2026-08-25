"""What a recommendation has brought, once it has come good.

`users.source` is the record of who brought whom: written once at registration,
never rewritten, because "who brought them" does not change. This module is
about the other half — the moment that record turns into something owed, which
by the owner's rule is the friend's first order that was not cancelled.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Earned:
    """One referral that has come good and has not been paid for.

    A dataclass rather than the pair it replaces, and the reason is that the
    two fields are both ints and both chat ids: transposed, a tuple still
    typechecks, the reward row is written backwards, and the thank-you goes to
    the person who was invited instead of the one who did the inviting.
    Nothing downstream would look wrong. `Waiting` in core/domain/stock.py was
    made a type for exactly this reason and the argument is the same one.

    Both are `chat_id`, not the surrogate `users.id`: this is read and written
    outside any transaction, which is where the identity rule in
    core/ports/users.py puts the chat id.
    """

    friend_chat_id: int
    referrer_chat_id: int
