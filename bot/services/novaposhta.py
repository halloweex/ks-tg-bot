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

    Accepts one key or several.

    Measured against real TTNs spanning 2024-2026, including parcels from
    different sender prefixes: every one of the six accounts' keys resolved every
    TTN. It is the supplied phone that authorises the lookup, not which account
    created the parcel — so **one key is enough**, and the loop below exits on
    the first one every time.

    Several keys are still accepted, and the loop is kept as failover: a revoked
    or rate-limited key falls through to the next instead of silently showing the
    customer nothing.
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
                last_status_error: httpx.HTTPStatusError | None = None
                for api_key in self._key_order(ttn):
                    try:
                        doc = await self._track_with(client, api_key, ttn, phone)
                    except httpx.HTTPStatusError as exc:
                        # 401, 403 and 429 are answers about this key, so the
                        # next one is worth trying — this is the failover the
                        # docstring above promises. Until this except existed,
                        # raise_for_status() threw straight out of the loop and
                        # a single rate-limited key hid the parcel entirely.
                        #
                        # Deliberately narrower than httpx.HTTPError: a connect
                        # error or a timeout is about the host, and every key
                        # posts to the same API_URL, so retrying would only
                        # multiply a 10-second wait by the number of keys —
                        # inside a Telegram handler that already loops over
                        # every parcel in the order list.
                        last_status_error = exc
                        doc = None
                        continue
                    if doc is not None:
                        self._key_for_ttn[ttn] = api_key
                        break
                if doc is None:
                    if last_status_error is not None:
                        logger.error(
                            "Nova Poshta: every key failed for TTN {}, last: {}",
                            ttn, last_status_error,
                        )
                    else:
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
