"""Telegram's failures, translated into the three answers the sender needs.

This is the half of the outbox that cannot be tested without knowing aiogram,
which is exactly why it is a separate file from the scenario: everything about
what to *do* with a failure lives in core.usecases.notify and needs no token,
and everything about recognising one lives here.
"""
from __future__ import annotations

import asyncio

import pytest
from aiogram.exceptions import (TelegramBadRequest, TelegramForbiddenError,
                                TelegramRetryAfter)

from bot.outbox import TelegramNotifier
from core.ports.notifier import RateLimited, RecipientGone

CHAT = 555
PAYLOAD = {"text": "your cream is back"}


class FakeBot:
    """Records the call, or raises what the test told it to."""

    def __init__(self, *raises) -> None:
        self.calls: list[dict] = []
        self._raises = list(raises)

    async def send_message(self, chat_id: int, text: str, **kw):
        self.calls.append({"chat_id": chat_id, "text": text, **kw})
        if self._raises:
            raise self._raises.pop(0)


@pytest.fixture(autouse=True)
def no_pacing(monkeypatch):
    """The transport paces itself 50ms per message; the tests are about which
    calls happen, not about wall clock."""
    real_sleep = asyncio.sleep
    monkeypatch.setattr(asyncio, "sleep", lambda _s: real_sleep(0))


def _send(bot: FakeBot, payload: dict | None = None, *, silent: bool = False):
    return asyncio.run(
        TelegramNotifier(bot).send(CHAT, payload or PAYLOAD, silent=silent)
    )


def test_a_message_reaches_telegram_with_its_silence_flag():
    bot = FakeBot()
    _send(bot, silent=True)
    assert bot.calls[0]["chat_id"] == CHAT
    assert bot.calls[0]["disable_notification"] is True


def test_a_rate_limit_becomes_the_wait_it_names():
    """The number is the point: the sender reschedules by it instead of
    guessing, and guessing is what earns the next 429."""
    bot = FakeBot(TelegramRetryAfter(method=None, message="Too Many Requests",
                                     retry_after=42))
    with pytest.raises(RateLimited) as caught:
        _send(bot)
    assert caught.value.retry_after == 42


def test_a_block_becomes_a_recipient_who_is_gone():
    bot = FakeBot(TelegramForbiddenError(method=None,
                                         message="Forbidden: bot was blocked by the user"))
    with pytest.raises(RecipientGone):
        _send(bot)


@pytest.mark.parametrize(
    "message",
    [
        "Bad Request: chat not found",
        "Bad Request: user is deactivated",
        "Bad Request: PEER_ID_INVALID",
    ],
)
def test_the_bad_requests_that_mean_gone_are_recognised(message):
    """aiogram reports all of them as one exception type, so the text is the
    only thing that separates "this chat is over" from "this call was wrong"."""
    bot = FakeBot(TelegramBadRequest(method=None, message=message))
    with pytest.raises(RecipientGone):
        _send(bot)


def test_any_other_bad_request_stays_unknown():
    """Left as it is, so the sender treats it as "try again later" rather than
    quietly retiring a chat over a message nobody has read yet."""
    bot = FakeBot(TelegramBadRequest(method=None, message="Bad Request: message is too long"))
    with pytest.raises(TelegramBadRequest):
        _send(bot)


def test_a_rejected_message_effect_costs_the_effect_and_not_the_message():
    """An effect id Telegram stops recognising must not cost anybody their
    notification — the fallback the stock watcher has had since the confetti."""
    bot = FakeBot(TelegramBadRequest(method=None,
                                     message="Bad Request: MESSAGE_EFFECT_ID_INVALID"))
    _send(bot, {"text": "back in stock", "effect_id": "5046509860389126442"})

    assert len(bot.calls) == 2
    assert bot.calls[0]["message_effect_id"] == "5046509860389126442"
    assert "message_effect_id" not in bot.calls[1]


def test_a_message_with_no_text_fails_instead_of_calling_telegram():
    """Not a transport failure, and retrying it five times would only prove
    that. It reaches the sender as unknown and lands on the shelf."""
    bot = FakeBot()
    with pytest.raises(ValueError):
        _send(bot, {"campaign_key": "stock.260819"})
    assert bot.calls == []
