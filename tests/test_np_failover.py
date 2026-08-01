"""A rate-limited Nova Poshta key must not hide the parcel.

Guards commit 4a9b4f7. Before it, raise_for_status() threw out of the loop over
keys, so a 429 on the first key ended the lookup and the delivery screen fell
back to week-old CRM data in the same layout as live tracking.
"""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from bot.services.novaposhta import NovaPoshtaClient

TTN = "20450000000001"
KEYS = ["k1", "k2", "k3"]
GOOD = {
    "success": True,
    "data": [{
        "StatusCode": "7",
        "Status": "Прибув у відділення",
        "CityRecipient": "Київ",
        "WarehouseRecipient": "№1",
        "ScheduledDeliveryDate": "",
        "ActualDeliveryDate": "",
        "DateCreated": "",
    }],
}


@pytest.fixture()
def asked(monkeypatch):
    """Records which keys were tried; the handler is set per test."""
    calls: list[str] = []
    box: dict = {}

    def install(handler):
        def wrapped(request: httpx.Request) -> httpx.Response:
            calls.append(json.loads(request.content)["apiKey"])
            return handler(request)

        transport = httpx.MockTransport(wrapped)
        real = httpx.AsyncClient
        monkeypatch.setattr(
            httpx, "AsyncClient", lambda *a, **kw: real(transport=transport)
        )

    box["install"] = install
    box["calls"] = calls
    return box


def _track(phone: str = "+380670000000"):
    return asyncio.run(NovaPoshtaClient(list(KEYS)).track(TTN, phone))


def test_falls_through_to_the_next_key_on_429(asked):
    asked["install"](
        lambda r: httpx.Response(429, json={"message": "Too Many Requests"})
        if json.loads(r.content)["apiKey"] == "k1"
        else httpx.Response(200, json=GOOD)
    )
    status = _track()
    assert status is not None, "a rate-limited first key hid the parcel"
    assert status.status_code == 7
    assert asked["calls"] == ["k1", "k2"]


def test_all_keys_rate_limited_returns_none_after_trying_all(asked):
    asked["install"](lambda r: httpx.Response(429, json={"message": "Too Many"}))
    assert _track() is None
    assert asked["calls"] == KEYS


def test_unreachable_host_stops_at_the_first_key(asked):
    """Deliberately narrower than httpx.HTTPError.

    Every key posts to the same API_URL, so walking them on a connect error
    would only multiply a 10-second timeout by the number of keys — inside a
    handler that already loops over every parcel in the order list.
    """

    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("host unreachable", request=request)

    asked["install"](boom)
    assert _track() is None
    assert asked["calls"] == ["k1"]


def test_key_that_cannot_see_the_parcel_is_skipped(asked):
    """200 with an empty StatusCode means "not this key", not "no parcel"."""
    asked["install"](
        lambda r: httpx.Response(200, json={"success": True, "data": [{"StatusCode": ""}]})
        if json.loads(r.content)["apiKey"] == "k1"
        else httpx.Response(200, json=GOOD)
    )
    assert _track() is not None
    assert asked["calls"] == ["k1", "k2"]
