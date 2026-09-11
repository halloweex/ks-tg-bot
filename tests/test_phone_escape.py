"""The share-phone screen has an exit, and it is not the button that failed.

A contact arrives, the number inside it does not parse, and the bot used to
answer «введи у міжнародному форматі» — advice its own next handler refuses,
because a typed number cannot prove ownership. Two messages contradicting each
other, and no third way out: menu keys are filtered out while a number is being
shared. These tests are about that loop staying closed.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, time, timedelta, timezone
from types import SimpleNamespace

import pytest

from bot.handlers import onboarding, settings
from bot.keyboards import share_phone_kb
from bot.states import OnboardingStates, SettingsStates, SupportStates
from core.domain.quiet import SHOP_TZ
from core.i18n import SUPPORTED, Texts

SENDER_ID = 555000111
T = Texts("uk")


@pytest.fixture(autouse=True)
def events(monkeypatch):
    """Collect analytics instead of spawning writes into a loop that is about
    to close — the convention the other handler tests follow."""
    collected: list[tuple] = []
    for module in ("onboarding", "settings"):
        monkeypatch.setattr(
            f"bot.handlers.{module}.track",
            lambda chat_id, event, **meta: collected.append((event, meta)),
        )
    return collected


class _Recorder:
    """A message that remembers what was answered, and the state it was in."""

    def __init__(self, *, text: str | None = None, phone: str | None = None):
        self.text = text
        self.chat = SimpleNamespace(id=SENDER_ID)
        self.from_user = SimpleNamespace(id=SENDER_ID)
        self.contact = (
            SimpleNamespace(phone_number=phone, user_id=SENDER_ID) if phone else None
        )
        self.answers: list[tuple[str, object]] = []

    async def answer(self, text, reply_markup=None, **kwargs):
        self.answers.append((text, reply_markup))
        return SimpleNamespace(message_id=1)


class _State:
    def __init__(self):
        self.state = None
        self.data: dict = {}

    async def set_state(self, state):
        self.state = state

    async def clear(self):
        self.state = None
        self.data = {}

    async def update_data(self, **kwargs):
        self.data.update(kwargs)
        return dict(self.data)

    async def get_data(self) -> dict:
        return dict(self.data)


def _config(start: time, end: time) -> SimpleNamespace:
    return SimpleNamespace(
        support_hours_from=start.strftime("%H:%M"),
        support_hours_to=end.strftime("%H:%M"),
        support_window=(start, end),
    )


def _window_open_now() -> SimpleNamespace:
    """A window around this very moment, on the shop's own clock."""
    here = datetime.now(timezone.utc).astimezone(SHOP_TZ)
    return _config((here - timedelta(hours=1)).time(),
                   (here + timedelta(hours=1)).time())


def _window_shut_now() -> SimpleNamespace:
    """A window that opens two hours from now, so this moment is outside it."""
    here = datetime.now(timezone.utc).astimezone(SHOP_TZ)
    return _config((here + timedelta(hours=2)).time(),
                   (here + timedelta(hours=3)).time())


def _labels(markup) -> list[str]:
    return [button.text for row in markup.keyboard for button in row]


def test_an_unreadable_number_offers_the_manager():
    """The one outcome where sharing again cannot help."""
    message = _Recorder(phone="not-a-number")
    asyncio.run(onboarding.process_contact(
        message, _State(), config=SimpleNamespace(), keycrm=None, t=T))

    text, markup = message.answers[-1]
    assert text == T.ERR_INVALID_PHONE
    assert T.BTN_SUPPORT in _labels(markup), "no way out of the loop"
    assert T.BTN_SHARE_PHONE in _labels(markup), "and the retry is still there"


def test_changing_the_phone_offers_it_too():
    message = _Recorder(phone="not-a-number")
    asyncio.run(settings.process_new_contact(
        message, _State(), config=SimpleNamespace(), t=T))

    text, markup = message.answers[-1]
    assert text == T.ERR_INVALID_PHONE
    assert T.BTN_SUPPORT in _labels(markup)


@pytest.mark.parametrize("lang", sorted(SUPPORTED))
def test_no_language_asks_for_a_number_the_bot_refuses(lang):
    """The advice that started the loop: type it in this format."""
    text = Texts(lang).ERR_INVALID_PHONE
    assert "+380" not in text, "an example to type is an invitation to type"


@pytest.mark.parametrize(
    "module,state_group",
    [(onboarding, OnboardingStates), (settings, SettingsStates)],
    ids=["onboarding", "settings"],
)
def test_the_manager_key_is_answered_before_the_catch_all(module, state_group):
    """Registration order is the whole mechanism.

    The catch-all below it matches any message in the same state, so a handler
    registered after it never runs, and the button the bot itself drew would be
    answered with "the number cannot be typed".
    """
    names = [h.callback.__name__ for h in module.router.message.handlers]
    catch_all = next(n for n in names if n.startswith("reject_typed"))
    assert names.index("escape_to_support") < names.index(catch_all)


@pytest.mark.parametrize(
    "module", [onboarding, settings], ids=["onboarding", "settings"])
def test_pressing_the_manager_key_hands_over_to_support(module):
    message = _Recorder(text=T.BTN_SUPPORT)
    state = _State()
    asyncio.run(module.escape_to_support(message, state, _window_open_now(), t=T))

    assert state.state == SupportStates.waiting_message
    assert message.answers[-1][0] == T.MSG_SUPPORT_PROMPT


@pytest.mark.parametrize(
    "module", [onboarding, settings], ids=["onboarding", "settings"])
def test_the_escape_names_the_hour_when_nobody_is_there(module):
    """The escape is a door into support like any other, and at 23:40 it has to
    say what the other four doors say. It was the likeliest one to be forgotten:
    a number that would not parse is already a bad moment, and «розберемось
    разом» with nobody awake to read it makes it worse.

    The windows are built from the clock the code itself reads rather than from
    a frozen hour, because `escape_to_support` takes no `now` — so the test says
    "a window that is open right now" and "one that is not", and runs the same
    at any hour of any day."""
    shut = _window_shut_now()
    said_at_night = _Recorder(text=T.BTN_SUPPORT)
    asyncio.run(module.escape_to_support(said_at_night, _State(), shut, t=T))

    said_by_day = _Recorder(text=T.BTN_SUPPORT)
    asyncio.run(module.escape_to_support(
        said_by_day, _State(), _window_open_now(), t=T))

    assert said_by_day.answers[-1][0] == T.MSG_SUPPORT_PROMPT
    assert shut.support_window[0].strftime("%H:%M") in said_at_night.answers[-1][0]


@pytest.mark.parametrize(
    "module", [onboarding, settings], ids=["onboarding", "settings"])
def test_the_escape_is_attributed(module, events):
    """Support opened from here is not support opened from the menu: this one
    counts numbers Telegram gave us and we could not read."""
    asyncio.run(module.escape_to_support(
        _Recorder(text=T.BTN_SUPPORT), _State(), _window_open_now(), t=T))
    assert ("support_opened", {"source": "share_phone"}) in events


def test_a_typed_number_is_still_refused():
    """The exit must not become a hole: ownership still cannot be typed."""
    message = _Recorder(text="+380671234567")
    asyncio.run(onboarding.reject_typed_phone(message, t=T))
    assert message.answers[-1][0] == T.MSG_USE_SHARE_BUTTON


def test_the_plain_share_keyboard_has_one_key():
    """The happy path is untouched: the exit appears only after a failure."""
    assert _labels(share_phone_kb(T)) == [T.BTN_SHARE_PHONE]
