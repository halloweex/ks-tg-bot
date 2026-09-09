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
    """123 fields come back; eight are read. A trimmed fixture would prove nothing.

    RecipientDateTime used to be listed here as ignored and is not any more —
    it is what "✅ Отримано" is built from since the date fix.
    """
    raw = _body("tracking_in_transit.json")["data"][0]
    assert len(raw) > 100
    for ignored in ("CargoDescriptionString", "DocumentWeight",
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


# --- the two moments a delivered parcel has ---------------------------------
#
# The bug this pins: the screen printed ActualDeliveryDate under "✅ Отримано".
# That is the van reaching the branch. The customer collecting is
# RecipientDateTime, and on the parcel that prompted the fix the two were a day
# and three hours apart. Nothing covered this line, and a review proved it by
# mutation: renaming the key left all 751 tests green.


def test_a_delivered_parcel_maps_both_moments_separately():
    status = _track("tracking_delivered.json")
    raw = _body("tracking_delivered.json")["data"][0]
    assert status is not None
    assert status.recipient_date == raw["RecipientDateTime"]
    assert status.actual_delivery == raw["ActualDeliveryDate"]
    assert status.recipient_date != status.actual_delivery, (
        "the fixture must keep the two apart, or it pins nothing"
    )


def test_the_collected_moment_is_read_from_the_key_the_api_actually_sends():
    """Spelled out, because a typo in the key name is invisible otherwise: the
    field would simply stay empty and the screen would never say Отримано."""
    raw = _body("tracking_delivered.json")["data"][0]
    assert raw["RecipientDateTime"] == "30.07.2026 11:42:06"
    assert _track("tracking_delivered.json").recipient_date == "30.07.2026 11:42:06"


def test_a_parcel_in_transit_has_collected_empty():
    """The other half: nothing has been handed over, so the field is blank and
    the screen must fall through to "прибуло" or the estimate."""
    assert _track("tracking_in_transit.json").recipient_date == ""
