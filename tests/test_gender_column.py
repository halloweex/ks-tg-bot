"""users.gender: the column, the migration onto an existing database, and the
two ways it can be lost.

A cache of somebody else's decision, so the interesting cases are all about not
losing it and not inventing it: a phone change must not reset the form, a chat
nobody has decided about must read as the default rather than as unknown, and a
form must never bring a customer row into existence.
"""
from __future__ import annotations

import asyncio

import pytest

from core.domain.gender import Gender, form


@pytest.fixture()
def db(tmp_path, monkeypatch):
    from core.repos import base as repos_base

    monkeypatch.setattr(repos_base, "DB_PATH", str(tmp_path / "bot_data.db"))
    return repos_base


def test_a_fresh_database_has_the_column_and_nobody_in_it_has_a_form(db):
    from core.repos.schema import init_db
    from core.repos.users import get_user_gender, get_user_voice, save_user

    async def scenario():
        await init_db()
        await save_user(777, "+380670000001")
        return await get_user_gender(777), await get_user_voice(777)

    stored, voice = asyncio.run(scenario())
    assert stored is None
    assert voice == (None, None)
    # And None is the form the bot has always rendered, not the unmarked one.
    assert form(stored) is Gender.F


def test_the_column_arrives_on_a_database_that_predates_it(db):
    """Migration 20 against the shape production is in today: the column is
    dropped and the version wound back, which is as close to yesterday's
    database as a test can get."""
    import aiosqlite

    from core.repos.schema import SCHEMA_VERSION, init_db

    async def scenario():
        await init_db()
        async with aiosqlite.connect(db.DB_PATH) as conn:
            await conn.execute("ALTER TABLE users DROP COLUMN gender")
            await conn.execute("PRAGMA user_version = 19")
            await conn.commit()
            cursor = await conn.execute("PRAGMA table_info(users)")
            before = {row[1] for row in await cursor.fetchall()}
        await init_db()
        async with aiosqlite.connect(db.DB_PATH) as conn:
            cursor = await conn.execute("PRAGMA table_info(users)")
            after = {row[1] for row in await cursor.fetchall()}
            cursor = await conn.execute("PRAGMA user_version")
            version = (await cursor.fetchone())[0]
        return before, after, version

    before, after, version = asyncio.run(scenario())
    assert "gender" not in before
    assert "gender" in after
    assert version == SCHEMA_VERSION


def test_a_form_is_stored_and_read_back(db):
    from core.repos.schema import init_db
    from core.repos.users import (get_user_voice, save_user, set_user_gender,
                                  set_user_language)

    async def scenario():
        await init_db()
        await save_user(777, "+380670000001")
        await set_user_language(777, "en")
        await set_user_gender(777, Gender.M.value)
        return await get_user_voice(777)

    assert asyncio.run(scenario()) == ("en", "m")


def test_changing_a_number_does_not_change_how_somebody_is_addressed(db):
    """`save_user` is INSERT OR REPLACE, and «📱 Змінити номер» calls it. Every
    column it does not carry by hand is reset — which is how the language was
    nearly lost once already."""
    from core.repos.schema import init_db
    from core.repos.users import get_user_gender, save_user, set_user_gender

    async def scenario():
        await init_db()
        await save_user(777, "+380670000001")
        await set_user_gender(777, Gender.M.value)
        await save_user(777, "+380670000002")
        return await get_user_gender(777)

    assert asyncio.run(scenario()) == "m"


def test_a_form_never_brings_a_customer_into_existence(db):
    """A row in `users` means a verified number. The refresh sweeps chats it
    reads from that table, but a race — a chat that left between the read and
    the write — must not write a customer back in."""
    from core.repos.schema import init_db
    from core.repos.users import get_user_gender, set_user_gender

    async def scenario():
        await init_db()
        await set_user_gender(404, Gender.F.value)
        return await get_user_gender(404)

    assert asyncio.run(scenario()) is None


def test_the_adapter_is_the_two_functions(db):
    from core.repos.schema import init_db
    from core.repos.users import SqliteGenderForm, save_user

    async def scenario():
        await init_db()
        await save_user(777, "+380670000001")
        forms = SqliteGenderForm()
        before = await forms.form_for(777)
        await forms.remember(777, Gender.UNKNOWN.value)
        return before, await forms.form_for(777)

    assert asyncio.run(scenario()) == (None, "u")
