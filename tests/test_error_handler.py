"""The apology of last resort.

Before this, a handler that raised produced silence for the customer: aiogram
logged the traceback and the screen simply did not change.

The first version of these tests passed while the handler did not work. They
asserted that a tap gets `callback.answer(text, show_alert=True)` — which is
what the handler did, and which reaches nobody, because twenty-two of the
bot's twenty-five callback handlers answer the query on their own first line.
The tests pinned the implementation instead of the promise. The promise is that
**the customer is told**, and that is what these check now.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from aiogram.exceptions import TelegramBadRequest

from bot import errors

CHAT = 4242


class FakeBot:
    def __init__(self, fail: Exception | None = None) -> None:
        self.sent: list[tuple[int, str]] = []
        self._fail = fail

    async def send_message(self, chat_id, text, **kw):
        if self._fail:
            raise self._fail
        self.sent.append((chat_id, text))


class FakeCallback:
    """A tap. `answer_fails` is the normal case, not the exotic one: the query
    is usually already spent by the handler that raised."""

    def __init__(self, answer_fails: Exception | None = None,
                 chat_id: int = CHAT, language_code: str = "uk") -> None:
        self.answers: list[str] = []
        self.from_user = SimpleNamespace(id=CHAT, language_code=language_code)
        self.message = SimpleNamespace(chat=SimpleNamespace(id=chat_id))
        self._fail = answer_fails

    async def answer(self, text="", show_alert=False, **kw):
        if self._fail:
            raise self._fail
        self.answers.append(text)


class FakeInline:
    def __init__(self, language_code: str = "uk") -> None:
        self.answered: list[list] = []
        self.from_user = SimpleNamespace(id=CHAT, language_code=language_code)

    async def answer(self, results, **kw):
        self.answered.append(list(results))


def _config():
    return SimpleNamespace(env=SimpleNamespace(admin_ids=[1, 2]))


def _message(chat_id: int = CHAT, language_code: str = "uk"):
    return SimpleNamespace(
        chat=SimpleNamespace(id=chat_id),
        from_user=SimpleNamespace(id=CHAT, language_code=language_code),
    )


def _event(exc, *, callback=None, message=None, inline=None):
    return SimpleNamespace(
        exception=exc,
        update=SimpleNamespace(callback_query=callback, message=message,
                               edited_message=None, inline_query=inline),
    )


@pytest.fixture()
def wired(monkeypatch):
    """No database, no Telegram — just what the handler decided."""
    state = {"stored": "", "alerts": [], "lang_raises": False}

    async def fake_language(chat_id):
        if state["lang_raises"]:
            raise RuntimeError("the database is the thing that is broken")
        return state["stored"]

    async def fake_alert(bot, admin_ids, key, text):
        state["alerts"].append((key, text))
        return len(admin_ids)

    monkeypatch.setattr(errors, "get_user_language", fake_language)
    monkeypatch.setattr(errors, "tell_admins_once", fake_alert)
    return state


def _run(event, bot, config=None):
    return asyncio.run(errors.on_error(event, bot, config or _config()))


# --- the promise: the customer is told ---------------------------------------

def test_a_tap_is_told_by_message_because_the_popup_would_reach_nobody(wired):
    """The defect the review found: the handler that raised has almost always
    answered the query already, so a second answer is not delivery."""
    callback, bot = FakeCallback(), FakeBot()
    _run(_event(ValueError("boom"), callback=callback), bot)
    assert len(bot.sent) == 1, "the apology must arrive as a message"
    assert "Щось пішло не так" in bot.sent[0][1]


def test_a_spent_query_still_gets_the_apology_through(wired):
    """answer() raising is the expected case, not a failure of the handler."""
    callback = FakeCallback(
        answer_fails=TelegramBadRequest(method=None, message="query is too old"))
    bot = FakeBot()
    _run(_event(ValueError("boom"), callback=callback), bot)
    assert len(bot.sent) == 1, "a spent spinner must not swallow the apology"


def test_a_tap_is_answered_in_the_chat_it_came_from(wired):
    """Not from_user.id: a tap in the support group must not open a private
    chat the person never asked for."""
    callback, bot = FakeCallback(chat_id=-100500), FakeBot()
    _run(_event(ValueError("boom"), callback=callback), bot)
    assert bot.sent[0][0] == -100500


def test_a_typed_message_is_answered_in_its_chat(wired):
    bot = FakeBot()
    _run(_event(ValueError("boom"), message=_message()), bot)
    assert bot.sent[0][0] == CHAT
    assert "Щось пішло не так" in bot.sent[0][1]


def test_an_inline_query_at_least_stops_spinning(wired):
    """There is no apology an inline answer can carry, but an unanswered query
    leaves the panel spinning until Telegram times it out."""
    inline, bot = FakeInline(), FakeBot()
    _run(_event(ValueError("boom"), inline=inline), bot)
    assert inline.answered == [[]]
    assert bot.sent == [], "there is no chat to write into"
    assert len(wired["alerts"]) == 1


# --- language ----------------------------------------------------------------

def test_a_stored_choice_wins(wired):
    wired["stored"] = "en"
    callback, bot = FakeCallback(language_code="uk"), FakeBot()
    _run(_event(ValueError("boom"), callback=callback), bot)
    assert "Something went wrong" in bot.sent[0][1]


def test_without_a_stored_choice_telegram_decides(wired):
    """The middleware's precedence, which the first version of this handler did
    not follow: an English customer who never chose got Ukrainian."""
    wired["stored"] = ""
    callback, bot = FakeCallback(language_code="en-GB"), FakeBot()
    _run(_event(ValueError("boom"), callback=callback), bot)
    assert "Something went wrong" in bot.sent[0][1]


def test_a_broken_database_still_gets_an_apology_out(wired):
    """The language lookup reads the database, and the database can be why we
    are here. It must not become the second failure."""
    wired["lang_raises"] = True
    callback, bot = FakeCallback(language_code="uk"), FakeBot()
    _run(_event(ValueError("boom"), callback=callback), bot)
    assert "Щось пішло не так" in bot.sent[0][1]


# --- the admins --------------------------------------------------------------

def test_the_admins_hear_about_it_keyed_by_kind(wired):
    callback, bot = FakeCallback(), FakeBot()
    _run(_event(KeyError("products_json"), callback=callback), bot)
    key, text = wired["alerts"][0]
    assert key == "handler-error:KeyError", "keyed by type, so a storm is one alert"
    assert "KeyError" in text


def test_the_exception_text_is_escaped_before_it_becomes_html(wired):
    """The bot's default parse mode is HTML and an exception's text is
    arbitrary. Unescaped, Telegram answers 400, tell_admins swallows it, and
    the alert vanishes for exactly the failures worth reading."""
    callback, bot = FakeCallback(), FakeBot()
    _run(_event(ValueError("<b>unclosed & broken"), callback=callback), bot)
    _key, text = wired["alerts"][0]
    assert "<b>" not in text
    assert "&lt;b&gt;" in text and "&amp;" in text


def test_a_failed_apology_does_not_cost_the_admins_their_alert(wired):
    """She blocked the bot. That is not news — but the alert still matters, and
    the first version only caught TelegramAPIError here."""
    callback = FakeCallback()
    bot = FakeBot(fail=RuntimeError("the transport is gone"))
    assert _run(_event(ValueError("boom"), callback=callback), bot) is True
    assert len(wired["alerts"]) == 1


def test_an_update_nobody_is_waiting_on_still_alerts(wired):
    bot = FakeBot()
    assert _run(_event(ValueError("boom")), bot) is True
    assert bot.sent == []
    assert len(wired["alerts"]) == 1


def test_the_handler_swallows_its_own_failure(wired, monkeypatch):
    """Whatever happens in here, the original exception is already logged and
    raising again would lose the update as well."""
    def explode(*a, **kw):
        raise RuntimeError("the alerter is broken too")

    monkeypatch.setattr(errors, "tell_admins_once", explode)
    callback, bot = FakeCallback(), FakeBot()
    assert _run(_event(ValueError("boom"), callback=callback), bot) is True
    assert bot.sent, "the customer was still told before it fell over"


# --- the wiring, through a real dispatcher -----------------------------------
#
# Everything above calls on_error directly, which proves what it decides and
# nothing about whether aiogram will ever call it — or call it with the
# arguments its signature asks for. `config` comes from dp["config"], and a
# missing injection would raise *inside* the error handler, restoring exactly
# the silence this module exists to end.
#
# Both surfaces are exercised. The first version of this test covered only the
# message path, which is why it passed while every tap stayed silent.


class _NoNetwork:
    """A bot session that records calls instead of making them."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str | None]] = []

    async def __call__(self, bot, method, timeout=None):
        self.calls.append((type(method).__name__, getattr(method, "text", None)))
        return None

    async def close(self):
        pass


def _drive(update_factory, router):
    from aiogram import Bot, Dispatcher

    from bot.errors import on_error

    async def scenario():
        bot = Bot(token="123456:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA")
        session = _NoNetwork()
        bot.session = session
        dp = Dispatcher()
        dp["config"] = _config()
        dp.include_router(router)
        dp.error.register(on_error)
        await dp.feed_update(bot, update_factory())
        return session

    return asyncio.run(scenario())


@pytest.fixture()
def dispatcher_wired(monkeypatch):
    alerts: list[str] = []

    async def fake_language(chat_id):
        return "uk"

    async def fake_alert(bot, admin_ids, key, text):
        alerts.append(key)
        return 1

    monkeypatch.setattr(errors, "get_user_language", fake_language)
    monkeypatch.setattr(errors, "tell_admins_once", fake_alert)
    return alerts


def _chat_user_now():
    import datetime as dt

    from aiogram.types import Chat, User
    return (Chat(id=CHAT, type="private"),
            User(id=CHAT, is_bot=False, first_name="X"),
            dt.datetime.now(dt.timezone.utc))


def test_the_dispatcher_reaches_the_handler_for_a_message(dispatcher_wired):
    from aiogram import Router
    from aiogram.filters import Command
    from aiogram.types import Message, Update

    router = Router()

    @router.message(Command("boom"))
    async def boom(message):
        raise KeyError("products_json")

    chat, user, now = _chat_user_now()
    session = _drive(
        lambda: Update(update_id=1, message=Message(
            message_id=1, date=now, chat=chat, from_user=user, text="/boom")),
        router)

    assert [name for name, _ in session.calls] == ["SendMessage"]
    assert "Щось пішло не так" in session.calls[0][1]
    assert dispatcher_wired == ["handler-error:KeyError"]


def test_the_dispatcher_reaches_a_tap_that_already_answered(dispatcher_wired):
    """The real shape of the bug: the handler answers the query, then raises."""
    from aiogram import F, Router
    from aiogram.types import CallbackQuery, Message, Update

    router = Router()

    @router.callback_query(F.data == "boom")
    async def boom(callback):
        await callback.answer()        # the spinner comes off first, as everywhere
        raise KeyError("products_json")

    chat, user, now = _chat_user_now()
    session = _drive(
        lambda: Update(update_id=1, callback_query=CallbackQuery(
            id="q1", from_user=user, chat_instance="ci", data="boom",
            message=Message(message_id=1, date=now, chat=chat,
                            from_user=user, text="menu"))),
        router)

    methods = [name for name, _ in session.calls]
    assert "SendMessage" in methods, (
        "a tap that already answered must still reach the customer"
    )
    text = next(t for name, t in session.calls if name == "SendMessage")
    assert "Щось пішло не так" in text
    assert dispatcher_wired == ["handler-error:KeyError"]
