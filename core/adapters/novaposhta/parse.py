"""Nova Poshta response shapes: pure functions over decoded JSON, no transport.

The only one of the three adapters where this is not a move. There was no parser
here at all: both the envelope check and the field mapping lived inside
`async with httpx.AsyncClient`, so every test of the mapping had to stand up a
mock transport to reach it. The two functions below are those lines, lifted
verbatim, and the tests that used to fake a network now read a fixture.
"""
from __future__ import annotations

from dataclasses import dataclass


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

    Note what None does *not* mean: a TTN that does not exist comes back
    success=true with StatusCode 3, passes this guard, and reaches the screen as
    a real delivery status — docs/found-during-move.md §2. Fixing that is a
    change in behaviour and belongs in its own commit.
    """
    if not body.get("success") or not body.get("data"):
        return None
    doc = body["data"][0]
    # A key without access still answers 200 with a row that carries no
    # status, so an empty StatusCode means "not this key", not "no parcel".
    if not str(doc.get("StatusCode") or "").strip():
        return None
    return doc


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
