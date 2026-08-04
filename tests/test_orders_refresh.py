"""The handler's half of the refresh: find the number, then hand over.

What is left in bot/handlers/orders.py after the scenario moved to
core/usecases/sync_orders.py is the lookup, the semaphore and the call — and the
call is exactly where §13 broke. Between 2026-08-01 and 2026-08-02 it passed
three arguments to a function that took four, so every refresh raised TypeError
before reaching the network: silently under spawn, and invisibly on a cold cache
because the bot has no error handler at all.

The fake below therefore declares the real signature. A recorder that swallowed
*args would not have caught it.
"""
from __future__ import annotations

import asyncio

import pytest

from bot.handlers import orders as handler

CHAT = 555
PHONE = "+380670000000"


@pytest.fixture()
def called(monkeypatch):
    """Replaces the scenario with a recorder that has its exact signature."""
    calls: list[tuple] = []

    async def fake_sync(chat_id: int, phone: str, keycrm) -> None:
        calls.append((chat_id, phone, keycrm))

    async def fake_phone(chat_id: int) -> str:
        return PHONE

    monkeypatch.setattr(handler, "sync_orders", fake_sync)
    monkeypatch.setattr(handler, "get_user_phone", fake_phone)
    return calls


def test_the_refresh_reaches_the_scenario(called):
    """The regression: this raised TypeError and got no further."""
    keycrm = object()
    asyncio.run(handler._refresh_orders(CHAT, keycrm))
    assert called == [(CHAT, PHONE, keycrm)]


def test_the_number_is_looked_up_here_and_not_passed_in(called):
    """_refresh_orders takes chat_id, not the phone: it is spawned as a task,
    and a task argument lives in a frame that reaches the traceback of anything
    failing under it (commit 2a61ece)."""
    import inspect

    assert list(inspect.signature(handler._refresh_orders).parameters) == [
        "chat_id", "keycrm",
    ]


def test_a_user_without_a_phone_is_never_synced(called, monkeypatch):
    """Nobody is registered under an empty number, and asking anyway would
    return somebody else's nothing."""
    async def no_phone(chat_id: int):
        return None

    monkeypatch.setattr(handler, "get_user_phone", no_phone)
    asyncio.run(handler._refresh_orders(CHAT, object()))
    assert called == []


def test_concurrent_refreshes_are_bounded(called):
    """The semaphore is what keeps a post-broadcast burst from turning into a
    thousand simultaneous API calls."""
    assert handler._refresh_semaphore._value == 10
