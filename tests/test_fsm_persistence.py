"""Conversation state survives a restart.

The failure this closes: a customer taps Support, a deploy recreates the
container while they type, MemoryStorage is gone, and their message matches no
handler — there is no state-less text handler in bot/handlers/ — so the bot says
nothing at all.
"""
from __future__ import annotations

import asyncio

import pytest
from aiogram.fsm.storage.base import StorageKey

from core.repos import fsm as fsm_repo
from core.repos import base as repos_base
from core.repos.schema import init_db
from bot.fsm_storage import SQLiteStorage
from bot.states import SupportStates

KEY = StorageKey(bot_id=1, chat_id=555, user_id=555)
OTHER = StorageKey(bot_id=1, chat_id=666, user_id=666)


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(repos_base, "DB_PATH", str(tmp_path / "bot_data.db"))
    asyncio.run(init_db())
    return fsm_repo


def _restart() -> SQLiteStorage:
    """A new storage object over the same file — what a redeploy leaves behind."""
    return SQLiteStorage()


def test_state_survives_a_restart(db):
    asyncio.run(_restart().set_state(KEY, SupportStates.waiting_message))
    assert asyncio.run(_restart().get_state(KEY)) == SupportStates.waiting_message.state


def test_data_survives_a_restart(db):
    asyncio.run(_restart().set_data(KEY, {"broadcast_text": "привіт"}))
    assert asyncio.run(_restart().get_data(KEY)) == {"broadcast_text": "привіт"}


def test_state_and_data_do_not_overwrite_each_other(db):
    s = _restart()
    asyncio.run(s.set_state(KEY, SupportStates.waiting_message))
    asyncio.run(s.set_data(KEY, {"a": 1}))
    after = _restart()
    assert asyncio.run(after.get_state(KEY)) == SupportStates.waiting_message.state
    assert asyncio.run(after.get_data(KEY)) == {"a": 1}


def test_written_in_the_other_order_too(db):
    s = _restart()
    asyncio.run(s.set_data(KEY, {"a": 1}))
    asyncio.run(s.set_state(KEY, SupportStates.waiting_message))
    after = _restart()
    assert asyncio.run(after.get_state(KEY)) == SupportStates.waiting_message.state
    assert asyncio.run(after.get_data(KEY)) == {"a": 1}


def test_clearing_state_survives_too(db):
    s = _restart()
    asyncio.run(s.set_state(KEY, SupportStates.waiting_message))
    asyncio.run(s.set_state(KEY, None))
    assert asyncio.run(_restart().get_state(KEY)) is None


def test_conversations_do_not_bleed_into_each_other(db):
    s = _restart()
    asyncio.run(s.set_state(KEY, SupportStates.waiting_message))
    assert asyncio.run(s.get_state(OTHER)) is None
    assert asyncio.run(s.get_data(OTHER)) == {}


def test_every_field_of_the_key_separates_conversations(db):
    """thread_id, business_connection_id and destiny are separate conversations.

    Dropping any of them from the key would merge two people's flows.
    """
    s = _restart()
    base = StorageKey(bot_id=1, chat_id=7, user_id=7)
    variants = [
        base,
        StorageKey(bot_id=2, chat_id=7, user_id=7),
        StorageKey(bot_id=1, chat_id=7, user_id=7, thread_id=3),
        StorageKey(bot_id=1, chat_id=7, user_id=7, business_connection_id="b"),
        StorageKey(bot_id=1, chat_id=7, user_id=7, destiny="other"),
    ]
    for i, key in enumerate(variants):
        asyncio.run(s.set_state(key, f"state-{i}"))
    for i, key in enumerate(variants):
        assert asyncio.run(s.get_state(key)) == f"state-{i}"


def test_unreadable_data_does_not_break_the_conversation(db):
    """The state still routes the user; the flow re-asks rather than crashing."""
    asyncio.run(fsm_repo.fsm_save("1|555|555|||default", state="x", data="{not json"))
    assert asyncio.run(_restart().get_data(KEY)) == {}
    assert asyncio.run(_restart().get_state(KEY)) == "x"


def test_abandoned_conversations_are_swept(db):
    import sqlite3

    asyncio.run(_restart().set_state(KEY, SupportStates.waiting_message))
    conn = sqlite3.connect(repos_base.DB_PATH)
    conn.execute("UPDATE fsm_state SET updated_at = datetime('now', '-30 days')")
    conn.commit()
    conn.close()
    # Any write runs the sweep.
    asyncio.run(_restart().set_state(OTHER, SupportStates.waiting_message))
    assert asyncio.run(_restart().get_state(KEY)) is None
    assert asyncio.run(_restart().get_state(OTHER)) is not None
