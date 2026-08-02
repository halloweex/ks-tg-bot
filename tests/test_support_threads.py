"""A manager's reply reaches the customer, whichever message they replied to.

Before support_threads the target was guessed from the replied-to message, and
both guesses fail on the most common case: the manager replies to the forwarded
text, and the customer has forwarding privacy on, so there is no sender to read
and no metadata line under the cursor.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from core.repos import support as support_repo
from core.repos import base as repos_base
from core.repos.schema import init_db
from bot.handlers import support

SUPPORT_CHAT = 129462784
CUSTOMER = 555000111


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(repos_base, "DB_PATH", str(tmp_path / "bot_data.db"))
    asyncio.run(init_db())
    return support_repo


def _replied(message_id: int, *, text: str | None = None, forward_from_id: int | None = None,
             from_bot: bool = True):
    return SimpleNamespace(
        message_id=message_id,
        text=text,
        forward_from=SimpleNamespace(id=forward_from_id) if forward_from_id else None,
        from_user=SimpleNamespace(is_bot=from_bot),
    )


def test_all_three_messages_of_a_thread_map_to_the_customer(db):
    asyncio.run(db.remember_support_thread([10, 11, 12], CUSTOMER))
    for message_id in (10, 11, 12):
        assert asyncio.run(db.support_thread_owner(message_id)) == CUSTOMER


def test_unknown_message_has_no_owner(db):
    assert asyncio.run(db.support_thread_owner(999)) is None


def test_reply_to_the_forwarded_text_resolves(db):
    """The case both old guesses miss.

    Forwarding privacy strips forward_from, and the replied-to text is the
    customer's own words, which carry no chat_id.
    """
    asyncio.run(db.remember_support_thread([10, 11, 12], CUSTOMER))
    replied = _replied(11, text="де моє замовлення?")
    assert asyncio.run(support._reply_target(replied)) == CUSTOMER


@pytest.mark.parametrize(
    "replied,why",
    [
        (_replied(77, text="?", forward_from_id=CUSTOMER), "forward_from is not trusted"),
        (_replied(78, text=f"Нове звернення. chat_id: {CUSTOMER}"), "the text is not parsed"),
    ],
    ids=["forward-from", "metadata-regex"],
)
def test_guesses_are_gone_and_do_not_route_a_reply(db, replied, why):
    """Both used to be fallbacks and both were removed.

    A guess that is right most of the time is worse than an error here: the time
    it is wrong, a customer's message goes to a stranger. Threads older than the
    table are not migrated — there were three users — so they now produce the
    visible error instead.
    """
    assert asyncio.run(support._reply_target(replied)) is None, why


def test_no_target_when_nothing_identifies_the_customer(db):
    assert asyncio.run(support._reply_target(_replied(79, text="просто текст"))) is None
    assert asyncio.run(support._reply_target(None)) is None


def test_the_table_wins_over_a_stale_guess(db):
    """If both answer, the recorded mapping is the one to trust."""
    asyncio.run(db.remember_support_thread([80], CUSTOMER))
    replied = _replied(80, text="chat_id: 42", forward_from_id=42)
    assert asyncio.run(support._reply_target(replied)) == CUSTOMER


# --- what the customer actually receives ---------------------------------

class _FakeBot:
    def __init__(self):
        self.sent: list[dict] = []
        self.copied: list[dict] = []

    async def send_message(self, chat_id, text, **kw):
        self.sent.append({"chat_id": chat_id, "text": text})

    async def copy_message(self, chat_id, from_chat_id, message_id, **kw):
        self.copied.append({"chat_id": chat_id, "message_id": message_id})


def _manager_message(bot, *, text, replied):
    return SimpleNamespace(
        bot=bot, chat=SimpleNamespace(id=SUPPORT_CHAT), text=text,
        message_id=500, reply_to_message=replied,
        answer=lambda *a, **k: asyncio.sleep(0),
    )


@pytest.fixture()
def config():
    return SimpleNamespace(support_chat_id=SUPPORT_CHAT)


def test_text_reply_reaches_the_customer_as_one_message(db, config):
    asyncio.run(db.remember_support_thread([11], CUSTOMER))
    bot = _FakeBot()
    msg = _manager_message(bot, text="Вже відправили!", replied=_replied(11, text="?"))
    asyncio.run(support.admin_reply(msg, config, None))

    assert len(bot.sent) == 1 and not bot.copied
    assert bot.sent[0]["chat_id"] == CUSTOMER
    assert "Вже відправили!" in bot.sent[0]["text"]


def test_a_photo_reply_is_copied_instead_of_becoming_the_word_None(db, config):
    """Pinned defect, now fixed.

    The old handler always sent `message.text`, which is None for a photo, so
    the customer received the prefix followed by "None" and the manager saw
    nothing wrong.
    """
    asyncio.run(db.remember_support_thread([11], CUSTOMER))
    bot = _FakeBot()
    msg = _manager_message(bot, text=None, replied=_replied(11, text="?"))
    asyncio.run(support.admin_reply(msg, config, None))

    assert len(bot.copied) == 1, "the attachment itself must reach the customer"
    assert bot.copied[0] == {"chat_id": CUSTOMER, "message_id": 500}
    assert all("None" not in s["text"] for s in bot.sent)


def test_a_reply_in_another_chat_is_ignored(db, config):
    bot = _FakeBot()
    msg = _manager_message(bot, text="hi", replied=_replied(11, text="?"))
    msg.chat = SimpleNamespace(id=SUPPORT_CHAT + 1)
    asyncio.run(support.admin_reply(msg, config, None))
    assert not bot.sent and not bot.copied


# --- albums ---------------------------------------------------------------

class _ForwardingBot(_FakeBot):
    def __init__(self):
        super().__init__()
        self._next_id = 1000
        self.forwarded: list[int] = []

    async def send_message(self, chat_id, text, **kw):
        await super().send_message(chat_id, text, **kw)
        self._next_id += 1
        return SimpleNamespace(message_id=self._next_id)

    async def forward_message(self, chat_id, from_chat_id, message_id, **kw):
        self._next_id += 1
        self.forwarded.append(message_id)
        return SimpleNamespace(message_id=self._next_id)


def _customer_message(bot, *, message_id, media_group_id=None):
    answered: list[str] = []

    async def answer(text, **kw):
        answered.append(text)

    msg = SimpleNamespace(
        bot=bot, chat=SimpleNamespace(id=CUSTOMER), message_id=message_id,
        media_group_id=media_group_id, answer=answer,
    )
    msg.answered = answered
    return msg


class _NoState:
    async def clear(self):
        return None


@pytest.fixture()
def texts():
    return SimpleNamespace(MSG_SUPPORT_FORWARDED="ok")


def test_an_album_is_announced_once_and_every_part_forwarded(db, config, texts):
    """Three photos used to become one: the first cleared the state and the rest
    matched no handler at all."""
    bot = _ForwardingBot()
    parts = [_customer_message(bot, message_id=i, media_group_id="alb-1") for i in (1, 2, 3)]

    asyncio.run(support.forward_to_support(parts[0], _NoState(), config, texts))
    for part in parts[1:]:
        asyncio.run(support.forward_album_tail(part, _NoState(), config, texts))

    assert bot.forwarded == [1, 2, 3], "every photo must reach the manager"
    # Metadata line and instruction once, not three times.
    assert len(bot.sent) == 2
    # The customer is told once.
    assert parts[0].answered == ["ok"] and parts[1].answered == []


def test_every_part_of_an_album_can_be_replied_to(db, config, texts):
    bot = _ForwardingBot()
    parts = [_customer_message(bot, message_id=i, media_group_id="alb-2") for i in (1, 2)]
    asyncio.run(support.forward_to_support(parts[0], _NoState(), config, texts))
    asyncio.run(support.forward_album_tail(parts[1], _NoState(), config, texts))

    owners = {asyncio.run(support_repo.support_thread_owner(mid)) for mid in range(1001, 1006)}
    assert owners == {CUSTOMER, None} or owners == {CUSTOMER}
    assert CUSTOMER in owners


def test_an_unrelated_album_is_not_forwarded(db, config, texts):
    """Scoping matters: a photo sent later, outside a support flow, must not be
    quietly relayed to the manager."""
    bot = _ForwardingBot()
    msg = _customer_message(bot, message_id=9, media_group_id="never-started")
    asyncio.run(support.forward_album_tail(msg, _NoState(), config, texts))
    assert bot.forwarded == [] and bot.sent == []


def test_claiming_an_album_twice_reports_only_the_first(db):
    assert asyncio.run(support_repo.start_album(CUSTOMER, "alb-3")) is True
    assert asyncio.run(support_repo.start_album(CUSTOMER, "alb-3")) is False
    assert asyncio.run(support_repo.album_in_progress(CUSTOMER, "alb-3")) is True
    assert asyncio.run(support_repo.album_in_progress(CUSTOMER, "other")) is False


# --- the two cases the brief named separately -----------------------------

def test_an_unknown_thread_gives_the_manager_a_visible_error(db, config):
    """Silence is worse than an error: the manager believes they replied.

    This is the path a pre-table thread takes now that the guesses are gone.
    """
    bot = _FakeBot()
    complaints: list[str] = []

    async def answer(text, **kw):
        complaints.append(text)

    msg = _manager_message(bot, text="Вже відправили!", replied=_replied(4242, text="?"))
    msg.answer = answer
    asyncio.run(support.admin_reply(msg, config, None))

    assert complaints, "the manager must be told the reply went nowhere"
    assert not bot.sent and not bot.copied, "and nothing may be sent to anyone"


def test_a_reply_to_something_that_is_not_a_support_message_stays_quiet(db, config):
    """Not every reply in this chat is a support action; complaining would be noise."""
    bot = _FakeBot()
    complaints: list[str] = []

    async def answer(text, **kw):
        complaints.append(text)

    msg = _manager_message(bot, text="ok", replied=_replied(4242, text="?", from_bot=False))
    msg.answer = answer
    asyncio.run(support.admin_reply(msg, config, None))

    assert not complaints and not bot.sent


def test_attachments_travel_in_both_directions(db, config, texts):
    """Customer to manager rides forward_message, which carries any content;
    manager to customer rides copy_message. Both are asserted here so the pair
    cannot regress independently."""
    bot = _ForwardingBot()

    # A voice note from the customer: no text at all.
    incoming = _customer_message(bot, message_id=77)
    asyncio.run(support.forward_to_support(incoming, _NoState(), config, texts))
    assert bot.forwarded == [77], "the customer's attachment must be forwarded as-is"

    forwarded_id = 1002  # note=1001, forward=1002 with _ForwardingBot's counter
    assert asyncio.run(support_repo.support_thread_owner(forwarded_id)) == CUSTOMER

    # A photo back from the manager: message.text is None.
    reply = _manager_message(bot, text=None, replied=_replied(forwarded_id, text=None))
    asyncio.run(support.admin_reply(reply, config, None))
    assert bot.copied and bot.copied[-1]["chat_id"] == CUSTOMER
