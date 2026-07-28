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
    """Async client for the Nova Poshta Tracking API."""

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    async def track(self, ttn: str, phone: str = "") -> TrackingStatus | None:
        """Get tracking status for a single TTN.

        Returns None on any error (never raises).
        """
        payload = {
            "apiKey": self._api_key,
            "modelName": "TrackingDocument",
            "calledMethod": "getStatusDocuments",
            "methodProperties": {
                "Documents": [
                    {"DocumentNumber": ttn, "Phone": phone.replace("+", "")},
                ],
            },
        }

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(API_URL, json=payload, timeout=10.0)
                response.raise_for_status()
                data = response.json()

                if not data.get("success") or not data.get("data"):
                    logger.warning("Nova Poshta: no data for TTN {}", ttn)
                    return None

                doc = data["data"][0]
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
