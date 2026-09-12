"""The one line in the bot that calls a customer by name.

`input_field_placeholder` on the menu keyboard is the greyed-out line in the
input field — the one place a name reads as warmth rather than as a form letter,
because it is not addressed at anything the customer is doing. It is also the one
string built from a Telegram profile field, which is free text somebody else
controls, so most of what is tested here is what happens when that field is not
a name.

Telegram caps the placeholder at 64 characters and answers a longer one with a
400 — which costs the whole message the keyboard rides on, not just the
keyboard. Hence the length tests.
"""
from __future__ import annotations

import asyncio

import pytest
from aiogram.types import User

from core import texts
from core.i18n import FORMS, SUPPORTED, Texts


@pytest.mark.parametrize("lang", sorted(SUPPORTED))
@pytest.mark.parametrize("gender", sorted(FORMS))
def test_no_name_is_the_line_that_was_always_there(lang, gender):
    t = Texts(lang, gender)
    assert t.menu_placeholder() == t.MSG_MENU_PLACEHOLDER
    assert t.menu_placeholder("") == t.MSG_MENU_PLACEHOLDER


def test_each_form_keeps_its_own_voice_when_it_gains_a_name():
    assert Texts("uk", "f").menu_placeholder("Анна") == "Красуне Анно, обери дію"
    assert (Texts("uk", "m").menu_placeholder("Ілля")
            == "О! Відчуваю харизму, Ілля. Обери дію")
    assert Texts("uk", "u").menu_placeholder("Анна") == "Анно, обери дію"
    assert Texts("en", "f").menu_placeholder("Anna") == "Anna, choose an action"


@pytest.mark.parametrize("given,addressed", [
    ("Анна", "Анно"),          # -а → -о, the overwhelming case in this base
    ("Оксана", "Оксано"),
    ("Тетяна", "Тетяно"),
    ("Микола", "Миколо"),      # a masculine name ending the same way
    ("Наталія", "Наталіє"),    # -ія → -іє
    ("Юлія", "Юліє"),
    ("Ілля", "Ілля"),          # -я that is not -ія: left in the nominative
    ("Андрій", "Андрій"),      # would be «Андрію»; the stem rules are not here
    ("Олег", "Олег"),
])
def test_ukrainian_names_are_addressed_in_the_vocative_where_the_rule_is_safe(
        given, addressed):
    """Two regular rules and no dictionary — see core/texts.py on why the rest
    is deliberately left alone: a mangled name reads as not knowing whose it
    is."""
    assert texts.vocative(texts.first_name(given)) == addressed
    assert addressed in Texts("uk", "u").menu_placeholder(given)


def test_a_latin_name_is_never_declined():
    """"Sara" is not a Ukrainian noun and "Saro" is not a name."""
    assert Texts("uk", "u").menu_placeholder("Sara") == "Sara, обери дію"


def test_only_the_first_token_is_used():
    """People put their whole name in that field, surname and all."""
    assert Texts("uk", "u").menu_placeholder("Анна Марія Петренко") == "Анно, обери дію"


@pytest.mark.parametrize("nonsense", ["", "   ", "12345", "❤️❤️", "...", "+380671234567"])
def test_a_profile_field_with_no_name_in_it_falls_back(nonsense):
    """«❤️❤️, обери дію» is not warmer than the line without a name."""
    t = Texts("uk", "f")
    assert t.menu_placeholder(nonsense) == t.MSG_MENU_PLACEHOLDER


def test_a_name_with_hearts_around_it_keeps_them():
    """The opposite case, and deliberate: «❤️Юля❤️» is what she calls herself,
    and editing somebody's name down to what we consider a name is worse than
    showing it."""
    assert "Юля" in Texts("uk", "u").menu_placeholder("❤️Юля❤️")


@pytest.mark.parametrize("name", [
    "Я" * 40,                 # letters, all inside the BMP: one unit each
    "Юля" + "🎉" * 40,        # a name with a letter and a lot of astral emoji
    "👩🏽‍🦰Оксана",             # a joined sequence in front of the name
])
@pytest.mark.parametrize("lang", sorted(SUPPORTED))
@pytest.mark.parametrize("gender", sorted(FORMS))
def test_no_form_can_exceed_the_limit_telegram_enforces(lang, gender, name):
    """Measured in UTF-16 code units, which is how Telegram counts.

    This is the test that was wrong first: it measured `len()`, so «Юля🎉🎉🎉…»
    passed at 39 characters and would have been refused at 69 units — a 400 that
    costs the whole message the keyboard rides on, not just the keyboard.
    """
    line = Texts(lang, gender).menu_placeholder(name)
    assert 1 <= texts.utf16_len(line) <= texts.PLACEHOLDER_MAX_LEN, (
        f"{texts.utf16_len(line)} units: {line!r}")


@pytest.mark.parametrize("name", ["🎉" * 40, "Юля" + "🎉" * 40, "👨‍👩‍👧‍👦Ліза"])
def test_a_cut_name_is_still_a_string_that_can_be_sent(name):
    """Cutting at a number of UTF-16 units rather than codepoints would split a
    surrogate pair, and a lone surrogate cannot be encoded at all — the send
    would fail in the encoder rather than at Telegram."""
    line = Texts("uk", "f").menu_placeholder(name)
    line.encode("utf-8")
    line.encode("utf-16")
    assert not any("\ud800" <= char <= "\udfff" for char in line)


def test_a_line_that_would_not_fit_is_given_up_rather_than_trimmed(monkeypatch):
    """And the guard for the day somebody lengthens the string: a trimmed name
    is a wrong name, so the nameless line wins instead."""
    monkeypatch.setattr(texts, "NAME_MAX_LEN_IN_PLACEHOLDER", 200)
    t = Texts("uk", "f")
    assert t.menu_placeholder("Я" * 200) == t.MSG_MENU_PLACEHOLDER


def test_the_keyboard_carries_it():
    from bot.keyboards import main_menu_kb

    t = Texts("uk", "m")
    assert (main_menu_kb(t, "Ілля").input_field_placeholder
            == "О! Відчуваю харизму, Ілля. Обери дію")
    assert main_menu_kb(t).input_field_placeholder == t.MSG_MENU_PLACEHOLDER


# --- the trap this is wired to avoid ----------------------------------------
def test_the_name_comes_from_whoever_sent_the_update(tmp_path, monkeypatch):
    """The middleware resolves it from `event_from_user`, which is the sender of
    any update — a message, a callback, an inline query alike."""
    from core.repos import base as repos_base

    monkeypatch.setattr(repos_base, "DB_PATH", str(tmp_path / "bot_data.db"))
    from bot.middlewares import LanguageMiddleware

    async def scenario():
        data = {"event_from_user": User(id=7, is_bot=False, first_name="Ілля",
                                       language_code="uk")}

        async def handler(event, data):
            return data["customer_name"]

        return await LanguageMiddleware()(handler, object(), data)

    assert asyncio.run(scenario()) == "Ілля"


def test_the_screen_never_reads_a_name_off_the_message_it_answers():
    """`send_main_menu` is called with `callback.message` by the settings
    screen, and that message was sent by the BOT — so `message.from_user` there
    is the bot itself, and a placeholder built from it would greet the customer
    by the shop's name. The name is passed in instead, and this is the guard
    that it stays that way."""
    import ast
    import pathlib

    tree = ast.parse(pathlib.Path("bot/screen.py").read_text(encoding="utf-8"))
    # Attribute access and not the word: the docstring above explains the trap
    # and has to be allowed to name it.
    reads = [node.lineno for node in ast.walk(tree)
             if isinstance(node, ast.Attribute) and node.attr == "from_user"]
    assert not reads, f"bot/screen.py reads from_user at lines {reads}"
