"""Nova Poshta responses through the client, because there is no parser to call.

Unlike KeyCRM and Shopify, this client has no pure parse function: building the
TrackingStatus happens inside `async with httpx.AsyncClient` (bot/services/
novaposhta.py). So these go through a mock transport. Extracting a parser is
part of the move; until then this is what pins the mapping.
"""
from __future__ import annotations

import asyncio
import json
import pathlib

import httpx
import pytest

from bot.services.novaposhta import NovaPoshtaClient

FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "novaposhta"


def _client(monkeypatch, fixture: str):
    body = json.loads((FIXTURES / fixture).read_text())
    transport = httpx.MockTransport(lambda r: httpx.Response(200, json=body))
    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **kw: real(transport=transport))
    return NovaPoshtaClient(["k1"])


def _track(monkeypatch, fixture: str):
    return asyncio.run(_client(monkeypatch, fixture).track("20450000000001", "+380670000000"))


def test_in_transit_maps_every_field_the_screen_shows(monkeypatch):
    status = _track(monkeypatch, "tracking_in_transit.json")
    raw = json.loads((FIXTURES / "tracking_in_transit.json").read_text())["data"][0]
    assert status is not None
    assert status.status_code == int(raw["StatusCode"])
    assert status.status == raw["Status"]
    assert status.city_recipient == raw["CityRecipient"]
    assert status.warehouse_recipient == raw["WarehouseRecipient"]
    assert status.scheduled_delivery == raw["ScheduledDeliveryDate"]
    assert status.actual_delivery == raw["ActualDeliveryDate"]
    assert status.date_created == raw["DateCreated"]


def test_the_fixture_keeps_the_fields_the_client_ignores():
    """123 fields come back; seven are read. A trimmed fixture would prove nothing."""
    raw = json.loads((FIXTURES / "tracking_in_transit.json").read_text())["data"][0]
    assert len(raw) > 100
    for ignored in ("RecipientDateTime", "CargoDescriptionString", "DocumentWeight",
                    "AnnouncedPrice", "PaymentMethod"):
        assert ignored in raw


def test_a_lookup_without_the_phone_still_returns_a_status(monkeypatch):
    """Measured, and worth knowing: the phone is not what authorises tracking here.

    The client always sends it (bot/services/novaposhta.py), and the class
    docstring says it is the phone that authorises the lookup. Against the live
    API the same TTN resolved with an empty phone as well.
    """
    assert _track(monkeypatch, "tracking_without_phone.json") is not None


def test_unknown_ttn_is_reported_as_a_real_status(monkeypatch):
    """Pins a defect — see docs/found-during-move.md.

    A TTN that does not exist answers success=true with StatusCode 3, not with
    an empty status, so the "empty StatusCode means this key cannot see it"
    guard never fires. The client hands the screen a TrackingStatus for a parcel
    that was never created.
    """
    status = _track(monkeypatch, "tracking_unknown_ttn.json")
    assert status is not None, "today a nonexistent TTN produces a status object"
    assert status.status_code == 3
