"""Nova Poshta response shapes: pure functions over decoded JSON, no transport.

The only one of the three adapters where this is not a move. There was no parser
here at all: both the envelope check and the field mapping lived inside
`async with httpx.AsyncClient`, so every test of the mapping had to stand up a
mock transport to reach it. The two functions below are those lines, lifted
verbatim, and the tests that used to fake a network now read a fixture.
"""
from __future__ import annotations

from dataclasses import dataclass


# Codes that mean there is no parcel behind this number, not a parcel with an
# early status. 3 is measured, not read: the live API answers a made-up number
# with success=true, StatusCode 3 and Status "Номер не знайдено". 2 is
# "Видалено" — an invoice the sender created and then deleted, which is equally
# nothing to track. Every other code describes a parcel that exists.
#
# Deliberately a small allowlist of absence rather than a full status table:
# Nova Poshta's documentation portal cannot be read programmatically (403 on
# developers.novaposhta.ua, TLS failure on devcenter), and a table copied from a
# third-party wrapper would be a guess wearing a citation. An unknown code
# therefore keeps the old behaviour — shown as a status — which is the failure
# mode we already understand.
NOT_FOUND_STATUS_CODES = frozenset({2, 3})


@dataclass
class TrackingStatus:
    """Parsed tracking result from Nova Poshta."""

    ttn: str
    status: str
    status_code: int
    city_recipient: str
    warehouse_recipient: str
    scheduled_delivery: str
    actual_delivery: str
    date_created: str


def tracking_document(body: dict) -> dict | None:
    """The one tracking row in a response. None means this key cannot see it.

    A row that comes back is not proof the parcel exists — see is_not_found,
    which is the question the caller has to ask next.
    """
    if not body.get("success") or not body.get("data"):
        return None
    doc = body["data"][0]
    # A key without access still answers 200 with a row that carries no
    # status, so an empty StatusCode means "not this key", not "no parcel".
    if not str(doc.get("StatusCode") or "").strip():
        return None
    return doc


def is_not_found(doc: dict) -> bool:
    """True when the carrier is saying there is no such parcel.

    Worth separating from "this key cannot see it": that one is about our
    credentials and the next key is worth trying, this one is about the number
    itself and every key will answer the same.
    """
    try:
        return int(doc.get("StatusCode", 0)) in NOT_FOUND_STATUS_CODES
    except (TypeError, ValueError):
        return False


def parse_tracking(ttn: str, doc: dict) -> TrackingStatus:
    """Seven fields out of the 123 the API sends.

    The TTN comes from the caller, not from the document: it is what was asked
    for, and the screen keys its parcels by it.
    """
    return TrackingStatus(
        ttn=ttn,
        status=doc.get("Status", ""),
        status_code=int(doc.get("StatusCode", 0)),
        city_recipient=doc.get("CityRecipient", ""),
        warehouse_recipient=doc.get("WarehouseRecipient", ""),
        scheduled_delivery=doc.get("ScheduledDeliveryDate", ""),
        actual_delivery=doc.get("ActualDeliveryDate", ""),
        date_created=doc.get("DateCreated", ""),
    )
