"""The number she shares when nothing is waiting for it.

«⚙️ Налаштування» → «📱 Змінити номер» sends a NEW message carrying the
share-phone reply keyboard, and the settings screen stays live above it. Tapping
«📋 Меню» there clears the state — but not the keyboard, because a keyboard
under the input field cannot be taken away by editing a message. So the button
the bot drew is still there, she taps it, and a contact arrives with no state
behind it.

Until this route existed, that was silence: both `F.contact` handlers sit behind
a state filter and nothing matched. She proved her number and the bot said
nothing at all.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from core.repos import base as repos_base
from core.repos.schema import init_db
from core.repos.users import get_user_phone

CHAT = 7171
STRANGER = 9999
E164 = "+380671234567"


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(repos_base, "DB_PATH", str(tmp_path / "bot_data.db"))
    asyncio.run(init_db())


class _Message:
    """As much of a Message as the handler touches."""

    def __init__(self, contact_user_id: int | None, number: str = E164) -> None:
        self.chat = SimpleNamespace(id=CHAT, type="private")
        self.from_user = SimpleNamespace(id=CHAT, language_code="uk")
        self.contact = SimpleNamespace(phone_number=number,
                                       user_id=contact_user_id)
        self.answers: list[dict] = []

    async def answer(self, text=None, reply_markup=None, **kw):
        self.answers.append({"text": text, "markup": reply_markup})
        return self


def _config():
    return SimpleNamespace(env=SimpleNamespace(admin_ids=[]),
                           bottom_menu=False, website_url="https://shop.example")


def _share(message) -> None:
    from core.i18n import Texts
    from bot.handlers.settings import contact_with_nobody_waiting_for_it

    asyncio.run(contact_with_nobody_waiting_for_it(
        message, _config(), Texts("uk")))


def test_her_own_number_is_saved_rather_than_ignored(db):
    message = _Message(contact_user_id=CHAT)
    _share(message)

    assert asyncio.run(get_user_phone(CHAT)) == E164, (
        "she proved her number and the bot did nothing with it")


def test_the_stray_keyboard_is_taken_away(db):
    """The one thing an edit could never do. `send_main_menu` carries a
    ReplyKeyboardRemove when bottom_menu is off, which is why the answer has to
    go through it rather than being a bare confirmation."""
    from aiogram.types import ReplyKeyboardRemove

    message = _Message(contact_user_id=CHAT)
    _share(message)

    markups = [a["markup"] for a in message.answers]
    assert any(isinstance(m, ReplyKeyboardRemove) for m in markups), (
        f"the keyboard she is stuck with was not removed: {markups}")


def test_somebody_elses_contact_card_never_becomes_her_number(db):
    """The IDOR boundary, and the reason this route may exist at all.

    `own_contact_phone` returns None unless Telegram says the contact's user_id
    is the sender's own. A forwarded card must not be able to attach another
    person's number — and therefore another person's orders — to this chat."""
    message = _Message(contact_user_id=STRANGER)
    _share(message)

    assert asyncio.run(get_user_phone(CHAT)) is None, (
        "a forwarded contact card was accepted as her verified number")
    assert message.answers == [], (
        "a card sent for some other reason is not addressed to us")


def test_a_contact_with_no_user_id_is_refused(db):
    """Telegram leaves user_id unset on a card typed in by hand rather than
    picked from the address book, and an unset owner is not an owner."""
    message = _Message(contact_user_id=None)
    _share(message)
    assert asyncio.run(get_user_phone(CHAT)) is None


def test_an_unreadable_number_changes_nothing(db):
    message = _Message(contact_user_id=CHAT, number="not a number")
    _share(message)
    assert asyncio.run(get_user_phone(CHAT)) is None


def test_the_route_does_not_shadow_the_flows_that_ask_for_a_number(db):
    """It is registered with StateFilter(None); onboarding and the settings
    change both sit behind their own states. Read off the routers rather than
    asserted about, because the failure would be that a number shared DURING
    onboarding stops registering the customer."""
    from aiogram.filters import StateFilter

    from bot.handlers import onboarding, settings

    gated = []
    for module in (onboarding, settings):
        for handler in module.router.message.handlers:
            names = [getattr(f.callback, "__name__", str(f.callback))
                     for f in handler.filters]
            if handler.callback.__name__ in ("process_contact",
                                             "process_new_contact"):
                gated.append((handler.callback.__name__, names))

    assert len(gated) == 2, f"the two flow handlers are still there: {gated}"
    for name, names in gated:
        assert any("State" in str(n) or "waiting" in str(n) for n in names), (
            f"{name} lost its state filter and now competes with the catch-all")
