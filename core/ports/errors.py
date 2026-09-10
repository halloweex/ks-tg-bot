"""What a scenario is allowed to know about an outside service failing.

§5 of `docs/components.md`: an error must not become an empty result. The three
API clients broke that invariant in the same way — they caught `httpx.HTTPError`
and returned `None`, `[]` or `{}` — and the cost was not abstract. Nova Poshta
unreachable meant the delivery screen quietly showed the shop's own stale
status as though it were current. KeyCRM unreachable meant "you have no orders"
to a customer who has them. Neither raised, so neither reached the error
handler, and the admins were told nothing.

One exception type, deliberately: a screen has exactly two answers for a
customer, "something broke" and "come back in a few minutes", and only the
second is a claim about the outside world. Splitting further would be inventing
distinctions no screen can act on.

It lives in `core.ports` because both ends of the conversation need it — the
adapter raises it, the scenario and the screen catch it — and a port is the one
place both may import. It imports nothing itself, which is what
`ports-are-only-signatures` requires.
"""
from __future__ import annotations


class Unavailable(Exception):
    """An outside service could not be reached, or refused to answer.

    Not "it answered and said no": a parcel Nova Poshta does not know is an
    answer, and so is a phone number with no orders behind it. Those are
    results. This is the absence of one.
    """

    def __init__(self, service: str, cause: Exception | None = None) -> None:
        super().__init__(f"{service} is unavailable: {cause}"
                         if cause else f"{service} is unavailable")
        self.service = service
        self.cause = cause
