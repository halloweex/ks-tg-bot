"""FSM state in SQLite instead of in the process.

aiogram defaults to MemoryStorage, so every deploy dropped every conversation
in flight. The visible case: a customer taps Support, the container is recreated
while they are typing, and their message then matches no handler at all — there
is no state-less text handler in bot/handlers/ — so the bot answers nothing.
They wrote to support and were met with silence, which for a beta of thirty
people is the difference between a working channel and a burnt one.

Not Redis: docs/architecture.md keeps it out until the web is multi-instance,
and the database is already here. The same class becomes a Postgres one at the
stage that moves the rest, by swapping the two statements below.
"""
from __future__ import annotations

import json
from typing import Any, Mapping

from aiogram.fsm.state import State
from aiogram.fsm.storage.base import BaseStorage, StorageKey
from loguru import logger

from bot.db import (fsm_delete, fsm_load, fsm_save)


def _key(key: StorageKey) -> str:
    """One string per conversation.

    Every field of StorageKey is included rather than just chat and user:
    thread_id and business_connection_id distinguish separate conversations in
    forum topics and business accounts, and destiny is how aiogram keeps
    independent state machines apart. Leaving any of them out would silently
    merge two conversations into one.
    """
    return "|".join((
        str(key.bot_id), str(key.chat_id), str(key.user_id),
        str(key.thread_id or ""), str(key.business_connection_id or ""),
        key.destiny,
    ))


class SQLiteStorage(BaseStorage):
    """Persistent FSM storage. Survives a restart; one process only.

    Single process is not a limitation this adds — the bot is already pinned to
    one instance because Telegram gives a second long-poller 409 Conflict. It is
    what makes the read-modify-write in update_data safe without locking.
    """

    async def set_state(self, key: StorageKey, state: Any = None) -> None:
        value = state.state if isinstance(state, State) else state
        await fsm_save(_key(key), state=value)

    async def get_state(self, key: StorageKey) -> str | None:
        state, _ = await fsm_load(_key(key))
        return state

    async def set_data(self, key: StorageKey, data: Mapping[str, Any]) -> None:
        await fsm_save(_key(key), data=json.dumps(dict(data), ensure_ascii=False))

    async def get_data(self, key: StorageKey) -> dict[str, Any]:
        _, raw = await fsm_load(_key(key))
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            # Unreadable data is not worth crashing a conversation over: the
            # state itself still routes the user, and the flow recovers by
            # asking again rather than by failing.
            logger.warning("FSM data for {} is not valid JSON, dropping it", _key(key))
            return {}

    async def close(self) -> None:
        return None


async def clear(key: StorageKey) -> None:
    """Drop a conversation's row outright. Used by tests and by /start."""
    await fsm_delete(_key(key))
