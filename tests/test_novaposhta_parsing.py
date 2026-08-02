"""Nova Poshta response mapping, read straight off the recordings.

Until the client was split there was no parser to call: the envelope check and
the field mapping both happened inside `async with httpx.AsyncClient`, so these
tests had to stand up a mock transport to reach one line of assignment. The
parser exists now and they read the fixture. What the client does with several
keys is a different question, pinned in test_np_failover.py.
"""
from __future__ import annotations

import json
import pathlib

import pytest

from core.adapters.novaposhta.parse import (is_not_found, parse_tracking,
                                            tracking_document)

FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "novaposhta"

# What the recordings were made with; the client passes the asked-for TTN
# through, so it is an argument rather than something read from the response.
TTN = "20450000000001"


def _body(fixture: str) -> dict:
    return json.loads((FIXTURES / fixture).read_text())


def _track(fixture: str):
    doc = tracking_document(_body(fixture))
    return None if doc is None else parse_tracking(TTN, doc)


def test_in_transit_maps_every_field_the_screen_shows():
    status = _track("tracking_in_transit.json")
    raw = _body("tracking_in_transit.json")["data"][0]
    assert status is not None
    assert status.ttn == TTN
    assert status.status_code == int(raw["StatusCode"])
    assert status.status == raw["Status"]
    assert status.city_recipient == raw["CityRecipient"]
    assert status.warehouse_recipient == raw["WarehouseRecipient"]
    assert status.scheduled_delivery == raw["ScheduledDeliveryDate"]
    assert status.actual_delivery == raw["ActualDeliveryDate"]
    assert status.date_created == raw["DateCreated"]


def test_the_fixture_keeps_the_fields_the_client_ignores():
    """123 fields come back; seven are read. A trimmed fixture would prove nothing."""
    raw = _body("tracking_in_transit.json")["data"][0]
    assert len(raw) > 100
    for ignored in ("RecipientDateTime", "CargoDescriptionString", "DocumentWeight",
                    "AnnouncedPrice", "PaymentMethod"):
        assert ignored in raw


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"success": False, "data": [{"StatusCode": "7"}]},
        {"success": True, "data": []},
        {"success": True, "data": [{"StatusCode": ""}]},
        {"success": True, "data": [{"StatusCode": "   "}]},
        {"success": True, "data": [{"StatusCode": None}]},
    ],
    ids=["empty", "success-false", "no-rows", "blank-code", "spaces", "null-code"],
)
def test_a_response_this_key_cannot_read_yields_no_document(body):
    """The guard the failover stands on: a key without access to the parcel
    answers 200 with a row carrying no status, and that must read as "try the
    next key", not as "the parcel does not exist"."""
    assert tracking_document(body) is None


def test_a_lookup_without_the_phone_still_returns_a_status():
    """Measured, and worth knowing: the phone is not what authorises tracking here.

    The client always sends it (core/adapters/novaposhta/client.py), and the
    class docstring says it is the phone that authorises the lookup. Against the
    live API the same TTN resolved with an empty phone as well, and this fixture
    is that response.
    """
    assert _track("tracking_without_phone.json") is not None


def test_a_number_the_carrier_does_not_know_is_recognised_as_absent():
    """Was defect §2 in docs/found-during-move.md.

    A TTN that does not exist answers success=true with StatusCode 3 and a row
    of 128 fields, so the "empty StatusCode means this key cannot see it" guard
    never fires and the row reaches the screen. What the customer lost to it was
    not a fake status — the screen printed "Номер не знайдено" honestly enough —
    but everything the CRM knew: the answer from the carrier took the place of
    "Доставлено, Київ, Відділення №12".
    """
    doc = tracking_document(_body("tracking_unknown_ttn.json"))
    assert doc is not None, "the row still arrives; it is what it says that changed"
    assert int(doc["StatusCode"]) == 3
    assert is_not_found(doc)


@pytest.mark.parametrize(
    "code,absent",
    [(2, True), (3, True), ("3", True), (1, False), (5, False), (7, False), (9, False),
     (102, False), ("", False), (None, False)],
    ids=["deleted", "not-found", "as-a-string", "created", "in-transit", "arrived",
         "received", "returning", "empty", "null"],
)
def test_only_the_two_absence_codes_count_as_absent(code, absent):
    """Everything outside the pair describes a parcel that exists, including
    codes we have never seen: an unknown code keeps the old behaviour rather
    than silently hiding somebody's real delivery."""
    assert is_not_found({"StatusCode": code}) is absent
