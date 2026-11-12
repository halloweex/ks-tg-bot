"""Who may send a broadcast: the admins, and the people named for it alone.

BROADCAST_USER_IDS exists because admin was the only way to reach /broadcast,
and admin is a lot more than that: a copy of every customer's support message,
every alert, /stats and /demo. Someone whose job is the newsletter should get
the one command and nothing else, so both halves are pinned here — the flow
goes through end to end for them, and the rest of the admin surface stays shut.

Every step is checked, not only the command: the Yes button and the typed "так"
are handlers of their own, and one that skipped the check would be the way
around it.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from bot.callbacks import BroadcastAction
from bot.handlers import broadcast as mod
from core.repos import base as repos_base
from core.repos.broadcast import get_unfinished_broadcasts
from core.repos.outbox import campaign_stats
from core.repos.schema import init_db
from core.repos.users import save_user
from core.usecases.broadcast import campaign_for

ADMIN = 4242
SENDER = 777          # in BROADCAST_USER_IDS, not an admin
STRANGER = 99
CUSTOMERS = (555, 556)
TEXT = "Нова колекція вже в магазині 🌸"

CONFIG = SimpleNamespace(env=SimpleNamespace(admin_ids=[ADMIN],
                                             broadcaster_ids=[SENDER]))


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(repos_base, "DB_PATH", str(tmp_path / "bot_data.db"))
    asyncio.run(init_db())
    for chat_id in CUSTOMERS:
        asyncio.run(save_user(chat_id, f"+38067000{chat_id}"))


class _State:
    def __init__(self) -> None:
        self.data: dict = {}
        self.state = None

    async def set_state(self, state) -> None:
        self.state = state

    async def update_data(self, **kwargs) -> dict:
        self.data.update(kwargs)
        return dict(self.data)

    async def get_data(self) -> dict:
        return dict(self.data)

    async def clear(self) -> None:
        self.data.clear()
        self.state = None


def _message(user_id: int, text: str, said: list[str]):
    async def answer(line, **kwargs):
        said.append(line)
        return SimpleNamespace(message_id=1)

    return SimpleNamespace(chat=SimpleNamespace(id=user_id),
                           from_user=SimpleNamespace(id=user_id),
                           text=text, answer=answer)


def _callback(user_id: int, edited: list[str]):
    async def answer(*args, **kwargs):
        return None

    async def edit_text(line, **kwargs):
        edited.append(line)

    return SimpleNamespace(from_user=SimpleNamespace(id=user_id), answer=answer,
                           message=SimpleNamespace(edit_text=edit_text))


def _queued(job_id: int) -> int:
    stats = asyncio.run(campaign_stats(str(campaign_for(job_id))))
    return sum(stats.values())


def test_a_named_sender_sends_a_broadcast_end_to_end(db):
    """The command, the text, the Yes button — and the messages are in the
    queue, under a job that is theirs, so the summary comes back to them."""
    state, said, edited = _State(), [], []

    asyncio.run(mod.cmd_broadcast(_message(SENDER, "/broadcast", said),
                                  CONFIG, state, None))
    assert said, "the prompt never came"

    asyncio.run(mod.process_broadcast_message(_message(SENDER, TEXT, said),
                                              CONFIG, state, None))
    assert state.data.get("broadcast_text") == TEXT

    asyncio.run(mod.process_broadcast_confirm(
        _callback(SENDER, edited), BroadcastAction(action="send"),
        CONFIG, state, None))

    jobs = asyncio.run(get_unfinished_broadcasts())
    assert [(job["text"], job["created_by"]) for job in jobs] == [(TEXT, SENDER)]
    assert _queued(jobs[0]["id"]) == len(CUSTOMERS)


def test_typing_tak_confirms_for_a_named_sender_too(db):
    state, said = _State(), []
    state.data["broadcast_text"] = TEXT

    asyncio.run(mod.process_broadcast_confirm_text(_message(SENDER, "так", said),
                                                   CONFIG, state, None))

    jobs = asyncio.run(get_unfinished_broadcasts())
    assert [job["created_by"] for job in jobs] == [SENDER]


def test_a_stranger_gets_nowhere_at_any_step(db):
    """Not the prompt, not the confirm step, and above all not a queued job —
    even holding state that says a text is waiting."""
    state, said, edited = _State(), [], []

    asyncio.run(mod.cmd_broadcast(_message(STRANGER, "/broadcast", said),
                                  CONFIG, state, None))
    asyncio.run(mod.process_broadcast_message(_message(STRANGER, TEXT, said),
                                              CONFIG, state, None))
    state.data["broadcast_text"] = TEXT
    asyncio.run(mod.process_broadcast_confirm(
        _callback(STRANGER, edited), BroadcastAction(action="send"),
        CONFIG, state, None))
    asyncio.run(mod.process_broadcast_confirm_text(_message(STRANGER, "так", said),
                                                   CONFIG, state, None))

    assert said == [] and edited == []
    assert asyncio.run(get_unfinished_broadcasts()) == []


def test_a_named_sender_is_not_an_admin(db):
    """The point of the separate list: /stats stays an admin's readout."""
    said: list[str] = []
    asyncio.run(mod.cmd_stats(_message(SENDER, "/stats", said), CONFIG))
    assert said == []


def test_the_variable_is_read_from_the_environment(monkeypatch):
    """BROADCAST_USER_IDS, comma-separated like ADMIN_USER_IDS; unset is nobody."""
    from core.config import EnvSettings

    monkeypatch.setenv("BOT_TOKEN", "1:test")
    monkeypatch.setenv("KEYCRM_API_KEY", "test")

    monkeypatch.setenv("BROADCAST_USER_IDS", "777, 888")
    assert EnvSettings().broadcaster_ids == [777, 888]

    monkeypatch.delenv("BROADCAST_USER_IDS")
    assert EnvSettings().broadcaster_ids == []
