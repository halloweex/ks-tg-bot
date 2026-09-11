"""The four screens the owner writes herself, checked before Telegram sees them.

`config.yaml` holds the Довідка pages — about, contacts, payment, delivery — as
HTML, and they are the only customer-facing copy in this project that nobody
reviews. Everything in `core/texts.py` goes through a commit; these go through
a text editor and a redeploy.

Two things could go wrong and neither was checked. A tag Telegram does not
allow costs that one screen and says so loudly, through `bot/errors.py`. A
broken YAML indent is worse and quieter: `load_config()` is called inside
`main()` (bot/__main__.py), so the deploy's "import the entrypoint" step never
reads the file, and the first anybody learns of it is the bot failing to come
up after a restart — which may be hours later, on somebody else's deploy.

So this file is deliberately about the config as it actually is on disk, not
about a fixture. It is the one test here that fails because of something
somebody typed rather than something somebody programmed.
"""
from __future__ import annotations

import re

import pytest
import yaml

from tests.conftest import REPO_ROOT

# Telegram's list, from the Bot API "HTML style" section. Anything outside it
# is a 400 on send — the message does not arrive at all.
#
# Written out rather than fetched, and kept short on purpose: a tag added here
# because the owner used it is a decision to support it, and the four screens
# below do not need more than they already use.
ALLOWED = frozenset({
    "b", "strong", "i", "em", "u", "ins", "s", "strike", "del",
    "span", "tg-spoiler", "tg-emoji", "a", "code", "pre", "blockquote",
})

#: The fields she edits. Discovered rather than listed would be tidier, but a
#: list is what makes a NEW owner-written field show up as a failing test
#: instead of quietly going unchecked.
OWNER_WRITTEN = ("about_text", "contacts_text", "payment_text", "delivery_text")

_TAG = re.compile(r"<\s*(/?)\s*([a-zA-Z][a-zA-Z0-9-]*)[^>]*?(/?)\s*>")


@pytest.fixture(scope="module")
def config() -> dict:
    """The real config.yaml, parsed on its own.

    Read directly rather than through `load_config()`, and that is not a
    shortcut: `load_config` also builds `EnvSettings`, which requires BOT_TOKEN
    and KEYCRM_API_KEY. Going through it made this file pass on a machine with
    a .env and fail everywhere else — which is precisely what it did the first
    time CI ran the suite, with twenty-five errors.

    These tests are about the YAML and the HTML inside it. Neither needs a
    token, and a test that needs one to read a file is a test that only one
    person can run."""
    with (REPO_ROOT / "config.yaml").open(encoding="utf-8") as handle:
        parsed = yaml.safe_load(handle)
    assert isinstance(parsed, dict), "config.yaml did not parse into a mapping"
    return parsed


def _tags(html: str) -> list[tuple[str, str]]:
    """(closing-slash, name) for every tag, in order."""
    return [(m.group(1), m.group(2).lower()) for m in _TAG.finditer(html)]


@pytest.mark.parametrize("field", OWNER_WRITTEN)
def test_every_tag_is_one_telegram_allows(config, field):
    """An unknown tag is not a cosmetic problem: Telegram refuses the whole
    message, so the screen does not arrive."""
    html = config.get(field) or ""
    used = {name for _slash, name in _tags(html)}
    unknown = used - ALLOWED
    assert not unknown, (
        f"{field} uses {sorted(unknown)}, which Telegram will refuse — "
        f"the page would not arrive at all")


@pytest.mark.parametrize("field", OWNER_WRITTEN)
def test_every_tag_is_closed(config, field):
    """A stray `<b>` swallows the rest of the page into bold, or is refused
    outright. Matched as a stack rather than by counting, because `<b><i></b>`
    balances by count and is still wrong."""
    html = config.get(field) or ""
    stack: list[str] = []
    for slash, name in _tags(html):
        if name == "br":
            continue
        if slash:
            assert stack, f"{field}: </{name}> with nothing open"
            opened = stack.pop()
            assert opened == name, f"{field}: <{opened}> closed by </{name}>"
        else:
            stack.append(name)
    assert not stack, f"{field}: never closed: {stack}"


@pytest.mark.parametrize("field", OWNER_WRITTEN)
def test_a_custom_emoji_keeps_something_to_fall_back_to(config, field):
    """`<tg-emoji>` draws a real logo, and only for a Premium account. The
    retry in bot/middlewares.py strips the tag and leaves what is inside it, so
    an empty one leaves a hole where the logo was — and nothing says so,
    because stripping it is what the fallback IS."""
    html = config.get(field) or ""
    for match in re.finditer(r"<tg-emoji\b[^>]*>(.*?)</tg-emoji>", html, re.S):
        assert match.group(1).strip(), (
            f"{field}: a <tg-emoji> with nothing inside it — the customer gets "
            f"a blank where the logo was the day Premium lapses")


@pytest.mark.parametrize("field", OWNER_WRITTEN)
def test_the_page_is_not_empty(config, field):
    """A field the owner blanked by accident is a screen with nothing on it,
    and the handler renders whatever it is given."""
    assert (config.get(field) or "").strip(), f"{field} is empty"


def test_the_pages_fit_in_one_message(config):
    """Telegram's limit is 4096 characters and these are edited by hand. Well
    under it today — the largest is about 400 — so this is a tripwire rather
    than a constraint she will feel."""
    for field in OWNER_WRITTEN:
        html = config.get(field) or ""
        assert len(html) < 4096, f"{field} is {len(html)} characters"
