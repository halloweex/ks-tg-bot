"""The settings screen shows the settings.

It used to name two of them and show the value of neither: "номер телефону, за
яким ми знаходимо твої замовлення, і мова бота" over two buttons. The one
question the screen exists to answer — which number does the shop have for me —
could be answered only by changing it.

These run the real helper against a real temporary database, because the thing
that can break is a call site that forgot `.format()`, and a customer reading
the literal «{phone}» is exactly what a test of the string alone would miss.
"""
from __future__ import annotations

import asyncio

import pytest

from bot.handlers.settings import settings_screen
from core.i18n import LANGUAGE_NAMES, SUPPORTED, Texts
from core.repos import base as repos_base
from core.repos.schema import init_db
from core.repos.users import save_user

CHAT = 555


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(repos_base, "DB_PATH", str(tmp_path / "bot_data.db"))
    asyncio.run(init_db())


def _screen(lang: str = "uk") -> str:
    text, _markup = asyncio.run(settings_screen(CHAT, Texts(lang)))
    return text


def test_the_number_on_file_is_the_number_shown(db):
    asyncio.run(save_user(CHAT, "+380671234567"))
    assert "+380671234567" in _screen()


def test_a_customer_with_no_number_is_told_how_to_give_one(db):
    """She can reach this screen before ever sharing a number, and telling her
    «твій номер:» with nothing after it would be worse than the old text."""
    said = _screen()
    assert "+3" not in said
    assert "{" not in said


@pytest.mark.parametrize("lang", sorted(SUPPORTED))
def test_no_placeholder_ever_reaches_the_customer(db, lang):
    """The screen carries `.format()` placeholders for the first time, and a
    call site that forgets one shows her a literal «{phone}»."""
    assert "{" not in _screen(lang)
    asyncio.run(save_user(CHAT, "+380671234567"))
    assert "{" not in _screen(lang)


@pytest.mark.parametrize("lang", sorted(SUPPORTED))
def test_the_language_is_named_the_way_its_own_button_names_it(db, lang):
    """LANGUAGE_NAMES holds endonyms and `language_kb` builds its labels from
    the same dict, so the screen and the button under it cannot disagree."""
    assert LANGUAGE_NAMES[lang] in _screen(lang)


def test_an_empty_string_counts_as_no_number(db):
    """The column is NOT NULL, so "no number" arrives as "" and never as NULL.
    `registered_phones` filters it the same way."""
    async def go() -> None:
        async with repos_base.connect() as conn:
            await conn.execute(
                "INSERT INTO users (chat_id, phone) VALUES (?, '')", (CHAT,))
            await conn.commit()
    asyncio.run(go())

    assert "{" not in _screen()
    assert "<code></code>" not in _screen()


def test_the_button_offers_what_the_sentence_above_it_offers(db):
    """The screen with no number says «поділись ним кнопкою нижче». A button
    labelled «Змінити номер» under that sentence offers to change something she
    has never given."""
    _text, markup = asyncio.run(settings_screen(CHAT, Texts("uk")))
    assert markup.inline_keyboard[0][0].text == Texts("uk").BTN_SHARE_PHONE

    asyncio.run(save_user(CHAT, "+380671234567"))
    _text, markup = asyncio.run(settings_screen(CHAT, Texts("uk")))
    assert markup.inline_keyboard[0][0].text == Texts("uk").BTN_CHANGE_PHONE
