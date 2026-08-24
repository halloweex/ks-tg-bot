"""The decoration: a reaction, a message that takes itself back, an effect.

All three are cosmetic, and the tests are mostly about that word — every one of
them has to fail into a plain, delivered message rather than into a broken
flow. A chat that looks slightly duller is never worth a customer not hearing
from the shop.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from aiogram.exceptions import TelegramBadRequest

from bot import screen


class _Method:
    """A stand-in for one aiogram call, recording what it was given."""

    def __init__(self, raises: Exception | None = None, returns=None) -> None:
        self.calls: list[dict] = []
        self._raises = raises
        self._returns = returns

    async def __call__(self, *args, **kwargs):
        self.calls.append({"args": args, **kwargs})
        if self._raises is not None:
            raise self._raises
        return self._returns


def _bad_request(message: str) -> TelegramBadRequest:
    return TelegramBadRequest(method=SimpleNamespace(), message=message)


# --- the reaction -----------------------------------------------------------

def test_a_message_is_marked_with_one_emoji():
    react = _Method()
    asyncio.run(screen.seen(SimpleNamespace(react=react)))
    assert [item.emoji for item in react.calls[0]["args"][0]] == ["👀"]


def test_a_refused_reaction_is_not_an_error_anybody_hears_about():
    """Telegram allows reactions only from a fixed set, and the set changes.
    The message was delivered either way — that is the part that matters."""
    react = _Method(raises=_bad_request("Bad Request: REACTION_INVALID"))
    asyncio.run(screen.seen(SimpleNamespace(react=react)))  # does not raise


# --- the message that goes away ---------------------------------------------

def test_a_message_takes_itself_back_after_it_has_been_read(monkeypatch):
    scheduled = []
    monkeypatch.setattr(screen, "spawn",
                        lambda coro, name=None: scheduled.append(coro))
    slept = []

    async def sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr(screen.asyncio, "sleep", sleep)

    delete = _Method()
    answer = _Method(returns=SimpleNamespace(delete=delete))

    async def run():
        await screen.ephemeral(SimpleNamespace(answer=answer), "ok", seconds=30)
        await scheduled[0]

    asyncio.run(run())
    assert answer.calls[0]["args"] == ("ok",)
    assert slept == [30]
    assert len(delete.calls) == 1


def test_a_message_already_gone_is_not_deleted_twice(monkeypatch):
    """The customer may have cleared the chat, or a redeploy may have outlived
    the timer. Neither is worth a line in the error log."""
    async def sleep(seconds):
        return None

    monkeypatch.setattr(screen.asyncio, "sleep", sleep)
    delete = _Method(raises=_bad_request("Bad Request: message to delete not found"))
    asyncio.run(screen._forget(SimpleNamespace(delete=delete), 1))  # does not raise


# --- the effect --------------------------------------------------------------

def test_an_effect_rides_along_when_telegram_takes_it():
    answer = _Method()
    asyncio.run(screen.with_effect(SimpleNamespace(answer=answer), "hi", "42"))
    assert answer.calls[0]["message_effect_id"] == "42"


def test_a_rejected_effect_costs_the_animation_and_not_the_message():
    """The ids are undocumented and read off a client (core/effects.py), so one
    of them going stale has to cost exactly this much."""
    sent: list[dict] = []

    async def answer(text, **kwargs):
        sent.append({"text": text, **kwargs})
        if "message_effect_id" in kwargs:
            raise _bad_request("Bad Request: MESSAGE_EFFECT_ID_INVALID")
        return SimpleNamespace()

    asyncio.run(screen.with_effect(SimpleNamespace(answer=answer), "hi", "42"))
    assert [call.get("message_effect_id") for call in sent] == ["42", None]
    assert sent[1]["text"] == "hi"


def test_anything_else_the_send_says_is_still_an_error():
    """Only the effect is optional. A message Telegram refuses for any other
    reason must not be swallowed by the fallback."""
    answer = _Method(raises=_bad_request("Bad Request: chat not found"))
    with pytest.raises(TelegramBadRequest):
        asyncio.run(screen.with_effect(SimpleNamespace(answer=answer), "hi", "42"))
