"""BROADCASTER_USER_IDS: shop staff who may send a broadcast and nothing else.

Before it, the only way to let a manager post was ADMIN_USER_IDS, and that list
is also who the bot wakes for a failing handler, a silent KeyCRM sync and a
shelf of parked messages — and who may run /stats and /demo. So these tests
are two halves: everything the broadcast flow needs reaches them, and nothing
else that admins have does.

Nothing is patched below the handler. The flow writes a job and queues rows in
a temporary database, and the summary is read back out of the outbox, because
"the person who sent it hears how it went" is a claim about those rows.
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from bot import profile
from bot.handlers import broadcast as mod
from core.i18n import Texts
from core.repos import base as repos_base
from core.repos.broadcast import SqliteBroadcastJournal, get_unfinished_broadcasts
from core.repos.outbox import SqliteMessageQueue, claim, mark_sent
from core.repos.schema import init_db
from core.repos.users import SqliteLanguageChoice, save_user
from core.usecases.broadcast import report_finished_jobs

ADMIN = 4242
STAFF = 1772368873
STRANGER = 99
CUSTOMERS = (555, 556, 557)
UK, EN = Texts("uk"), Texts("en")


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(repos_base, "DB_PATH", str(tmp_path / "bot_data.db"))
    asyncio.run(init_db())
    for chat_id in CUSTOMERS:
        asyncio.run(save_user(chat_id, f"+38067000{chat_id}"))


def _config():
    return SimpleNamespace(
        env=SimpleNamespace(admin_ids=[ADMIN], broadcaster_ids=[STAFF]),
        support_chat_id=-100,
    )


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


class _Message:
    def __init__(self, user_id: int, text: str = "") -> None:
        self.from_user = SimpleNamespace(id=user_id)
        self.chat = SimpleNamespace(id=user_id, type="private", title=None)
        self.text = text
        self.said: list[str] = []

    async def answer(self, text: str, **kwargs):
        self.said.append(text)
        return SimpleNamespace(message_id=1)


def _send_as(user_id: int, text: str, state: _State) -> list[str]:
    """/broadcast, the text, then «так» — the whole flow from one person."""
    said: list[str] = []
    for step, line in ((mod.cmd_broadcast, "/broadcast"),
                       (mod.process_broadcast_message, text),
                       (mod.process_broadcast_confirm_text, "так")):
        message = _Message(user_id, line)
        asyncio.run(step(message, _config(), state, None))
        said += message.said
    return said


def _drain_and_report() -> list[dict]:
    for row in asyncio.run(claim(50)):
        asyncio.run(mark_sent(row["id"]))
    asyncio.run(report_finished_jobs(
        SqliteBroadcastJournal(), SqliteLanguageChoice(), SqliteMessageQueue(),
        admins=(ADMIN,),
    ))
    return [row for row in asyncio.run(claim(50)) if row["type"] == "broadcast_report"]


# --- what they can do ---------------------------------------------------------

def test_staff_can_send_a_broadcast_start_to_finish(db):
    said = _send_as(STAFF, "Нова колекція вже в магазині 🌸", _State())

    assert said == [UK.MSG_BROADCAST_PROMPT,
                    UK.MSG_BROADCAST_CONFIRM.format(count=len(CUSTOMERS)),
                    UK.MSG_BROADCAST_STARTED]
    [job] = asyncio.run(get_unfinished_broadcasts())
    assert job["created_by"] == STAFF
    queued = asyncio.run(claim(50))
    assert sorted(row["chat_id"] for row in queued) == sorted(CUSTOMERS)


def test_the_summary_goes_back_to_staff_in_ukrainian(db):
    """admin_texts defaults to English, and that default is for the tooling:
    a manager who never opened the language screen would otherwise be greeted
    in a language nobody at the shop works in."""
    _send_as(STAFF, "Нова колекція", _State())

    [report] = _drain_and_report()
    assert report["chat_id"] == STAFF
    assert json.loads(report["payload"])["text"] == UK.MSG_BROADCAST_COMPLETE.format(
        sent=len(CUSTOMERS), failed=0, blocked=0)


def test_an_admin_keeps_the_admin_default(db):
    said = _send_as(ADMIN, "Нова колекція", _State())
    assert said[0] == EN.MSG_BROADCAST_PROMPT

    [report] = _drain_and_report()
    assert report["chat_id"] == ADMIN
    assert json.loads(report["payload"])["text"].startswith("Broadcast finished")


def test_the_command_guard_answers_staff_in_ukrainian(db):
    message = _Message(STAFF, "/stats")
    state = _State()
    asyncio.run(mod.process_broadcast_message(message, _config(), state, None))

    assert message.said == [UK.MSG_BROADCAST_LOOKS_LIKE_A_COMMAND]
    assert "broadcast_text" not in state.data


# --- what they cannot ---------------------------------------------------------

def test_a_stranger_gets_nothing(db):
    state = _State()
    said = _send_as(STRANGER, "Нова колекція", state)

    assert said == []
    assert state.state is None
    assert asyncio.run(get_unfinished_broadcasts()) == []


@pytest.mark.parametrize("handler", [mod.cmd_stats, mod.cmd_chatid])
def test_staff_are_not_admins(db, handler):
    message = _Message(STAFF, "/x")
    asyncio.run(handler(message, _config()))
    assert message.said == []


# --- the menu -----------------------------------------------------------------

class _Bot:
    def __init__(self) -> None:
        self.per_chat: dict[int, list[str]] = {}

    async def set_my_commands(self, commands, scope=None, language_code=None):
        chat_id = getattr(scope, "chat_id", None)
        if chat_id is not None:
            self.per_chat.setdefault(chat_id, []).append(
                ",".join(c.command for c in commands))

    async def set_chat_menu_button(self, **kwargs) -> None:
        pass

    async def set_my_short_description(self, **kwargs) -> None:
        pass

    async def set_my_description(self, **kwargs) -> None:
        pass


def test_staff_see_broadcast_in_their_command_list():
    bot = _Bot()
    asyncio.run(profile.apply(bot, [ADMIN], [STAFF]))

    [commands] = bot.per_chat[STAFF]
    assert commands.split(",") == ["start", "menu", "stop", "broadcast"]


def test_an_admin_listed_as_staff_too_keeps_the_admin_list():
    bot = _Bot()
    asyncio.run(profile.apply(bot, [ADMIN], [ADMIN, STAFF]))

    assert bot.per_chat[ADMIN] == [",".join(c for c, _ in profile.ADMIN_COMMANDS)]


# --- the setting --------------------------------------------------------------

def test_the_variable_reaches_the_setting(monkeypatch):
    """The field name is what maps it to BROADCASTER_USER_IDS: rename it and the
    mapping disappears silently, with an empty default underneath."""
    from core.config import EnvSettings

    monkeypatch.setenv("BOT_TOKEN", "1:test")
    monkeypatch.setenv("KEYCRM_API_KEY", "test")
    monkeypatch.setenv("BROADCASTER_USER_IDS", " 1772368873, 5 ,")
    assert EnvSettings().broadcaster_ids == [1772368873, 5]

    monkeypatch.setenv("BROADCASTER_USER_IDS", "")
    assert EnvSettings().broadcaster_ids == []
