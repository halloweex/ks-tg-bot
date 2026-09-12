"""`t` arrives at the handler already knowing who it is talking to.

The whole point of resolving the form in the middleware is that no handler can
forget to: there are forty-odd of them and one of these. So what is tested here
is the wiring and its failure mode, not the copy — tests/test_gender_texts.py
owns the copy.
"""
from __future__ import annotations

import asyncio

import pytest
from aiogram.types import User

from core import texts
from core.domain.gender import Gender
from core.i18n import UK_MASCULINE, UK_NEUTRAL


@pytest.fixture()
def db(tmp_path, monkeypatch):
    from core.repos import base as repos_base

    monkeypatch.setattr(repos_base, "DB_PATH", str(tmp_path / "bot_data.db"))
    return repos_base


def _resolved(chat_id: int, stored_form: str | None) -> object:
    """Run the middleware for one update and hand back the `t` it injected."""
    from bot.middlewares import LanguageMiddleware
    from core.repos.schema import init_db
    from core.repos.users import save_user, set_user_gender

    async def scenario():
        await init_db()
        await save_user(chat_id, "+380670000001")
        if stored_form is not None:
            await set_user_gender(chat_id, stored_form)

        data = {"event_from_user": User(id=chat_id, is_bot=False,
                                       first_name="Тест", language_code="uk")}

        async def handler(event, data):
            return data["t"]

        return await LanguageMiddleware()(handler, object(), data)

    return asyncio.run(scenario())


def test_nothing_stored_reads_exactly_as_the_bot_read_before(db):
    t = _resolved(777, None)
    assert t.MSG_MENU_PLACEHOLDER == texts.MSG_MENU_PLACEHOLDER
    assert t.GREETING == texts.GREETING


def test_a_man_is_addressed_as_one(db):
    t = _resolved(777, Gender.M.value)
    assert t.MSG_MENU_PLACEHOLDER == UK_MASCULINE["MSG_MENU_PLACEHOLDER"]
    assert "купував" in t.GREETING and "купувала" not in t.GREETING


def test_somebody_we_could_not_place_is_addressed_without_a_gender(db):
    t = _resolved(777, Gender.UNKNOWN.value)
    assert t.MSG_MENU_PLACEHOLDER == UK_NEUTRAL["MSG_MENU_PLACEHOLDER"]
    assert "купувала" not in t.GREETING and "купував" not in t.GREETING


def test_a_database_hiccup_does_not_change_how_anybody_is_addressed(db, monkeypatch):
    """The read sits on the path of every update. If it throws, the update must
    still be handled and the wording must be the one that was always there."""
    import bot.middlewares as middlewares
    from bot.middlewares import LanguageMiddleware

    async def boom(chat_id):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(middlewares, "get_user_voice", boom)

    async def scenario():
        data = {"event_from_user": User(id=777, is_bot=False, first_name="Тест",
                                       language_code="uk")}

        async def handler(event, data):
            return data["t"], data["lang"]

        return await LanguageMiddleware()(handler, object(), data)

    t, lang = asyncio.run(scenario())
    assert lang == "uk"
    assert t.MSG_MENU_PLACEHOLDER == texts.MSG_MENU_PLACEHOLDER
