"""No module uses a name that is not there.

The third shape of the same failure. §13 of docs/found-during-move.md was a call
with the wrong number of arguments, and tests/test_call_arity.py was written for
it. This is the neighbouring one: a name referenced in a function body that
nothing in scope provides. Python accepts both at import time and raises at the
first call, which for a handler means the first customer who taps the button.

It has already happened three times in one day — `config` in two handlers that
never declared it, and `get_cached_orders` called without being imported. All
three were on customer paths: the order history from either menu, and the
first-order offer at the end of registration. The suite was green throughout,
because handlers are thin on tests and a name error is invisible until called.

Only undefined names fail here. Unused imports and unused locals are real
smells and are deliberately not errors: a check that shouts about tidiness gets
switched off, and then it is not there for the one that matters. That is the
same reasoning tests/test_call_arity.py gives for staying conservative.
"""
from __future__ import annotations

from pyflakes import api, messages

from tests.conftest import REPO_ROOT

PACKAGES = ("bot", "core")

# The message classes that mean "this will raise when it runs", as opposed to
# "this is untidy".
FATAL = (
    messages.UndefinedName,
    messages.UndefinedLocal,
    messages.UndefinedExport,
)


class _Collector:
    """Pyflakes reporter that keeps the messages instead of printing them."""

    def __init__(self) -> None:
        self.flakes: list = []
        self.broken: list[str] = []

    def unexpectedError(self, filename, msg) -> None:
        self.broken.append(f"{filename}: {msg}")

    def syntaxError(self, filename, msg, lineno, offset, text) -> None:
        self.broken.append(f"{filename}:{lineno}: {msg}")

    def flake(self, message) -> None:
        self.flakes.append(message)


def _check() -> _Collector:
    collector = _Collector()
    api.checkRecursive(
        [str(REPO_ROOT / package) for package in PACKAGES], collector
    )
    return collector


def test_nothing_reads_a_name_that_is_not_in_scope():
    found = _check()
    assert not found.broken, "pyflakes could not read: " + "; ".join(found.broken)

    fatal = [str(m) for m in found.flakes if isinstance(m, FATAL)]
    assert not fatal, (
        "a name is used where nothing provides it — this raises at the first "
        "call, not at import:\n  " + "\n  ".join(fatal)
    )
