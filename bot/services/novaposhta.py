"""Nova Poshta Tracking API client for delivery status lookup."""
from __future__ import annotations

from dataclasses import dataclass

import httpx
from loguru import logger

API_URL = "https://api.novaposhta.ua/v2.0/json/"


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


class NovaPoshtaClient:
    """Async client for the Nova Poshta Tracking API.

    Takes every configured key. Parcels are created by six different legal
    entities, and a key may only see its own account's documents — so a TTN is
    tried against each key until one returns data, and the key that worked is
    remembered for that TTN so the scan happens once rather than on every view.

    If it turns out the supplied phone is what authorises the lookup, the first
    key answers everything and the loop simply never reaches the second.
    """

    def __init__(self, api_keys: list[str] | str) -> None:
        self._api_keys = [api_keys] if isinstance(api_keys, str) else list(api_keys)
        # ttn -> the key that last returned data for it
        self._key_for_ttn: dict[str, str] = {}

    def _key_order(self, ttn: str) -> list[str]:
        """Keys to try, the one known to work for this TTN first."""
        known = self._key_for_ttn.get(ttn)
        if not known:
            return self._api_keys
        return [known] + [k for k in self._api_keys if k != known]

    async def _track_with(
        self, client: httpx.AsyncClient, api_key: str, ttn: str, phone: str
    ) -> dict | None:
        """One (key, TTN) attempt. None means this key cannot see this parcel."""
        payload = {
            "apiKey": api_key,
            "modelName": "TrackingDocument",
            "calledMethod": "getStatusDocuments",
            "methodProperties": {
                "Documents": [
                    {"DocumentNumber": ttn, "Phone": phone.replace("+", "")},
                ],
            },
        }
        response = await client.post(API_URL, json=payload, timeout=10.0)
        response.raise_for_status()
        data = response.json()
        if not data.get("success") or not data.get("data"):
            return None
        doc = data["data"][0]
        # A key without access still answers 200 with a row that carries no
        # status, so an empty StatusCode means "not this key", not "no parcel".
        if not str(doc.get("StatusCode") or "").strip():
            return None
        return doc

    async def track(self, ttn: str, phone: str = "") -> TrackingStatus | None:
        """Get tracking status for a single TTN.

        Returns None on any error (never raises).
        """
        try:
            async with httpx.AsyncClient() as client:
                doc = None
                for api_key in self._key_order(ttn):
                    doc = await self._track_with(client, api_key, ttn, phone)
                    if doc is not None:
                        self._key_for_ttn[ttn] = api_key
                        break
                if doc is None:
                    logger.warning("Nova Poshta: no data for TTN {}", ttn)
                    return None

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

        except httpx.HTTPError as exc:
            logger.error("Nova Poshta HTTP error for TTN {}: {}", ttn, exc)
            return None
        except (KeyError, ValueError, IndexError) as exc:
            logger.error("Nova Poshta parse error for TTN {}: {}", ttn, exc)
            return None

    async def track_many(
        self, ttns: list[str], phone: str = ""
    ) -> dict[str, TrackingStatus]:
        """Track multiple TTNs. Returns {ttn: TrackingStatus} for successful lookups."""
        results: dict[str, TrackingStatus] = {}
        for ttn in ttns:
            status = await self.track(ttn, phone)
            if status:
                results[ttn] = status
        return results
